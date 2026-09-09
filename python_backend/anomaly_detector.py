"""
指标异常检测:**环比** 触发,**同时段基线** 复核。

思路:
    1. 环比 —— 每次抓到关键指标(库存、订单量等)后存入本地 state.json,
       下次运行时和上一次的数值比较,变化幅度超过阈值就**初步判定**为异常。
    2. 基线 —— 再用 history.py 里"最近 N 天同一时段的中位数"复核一遍:
       如果相对历史正常水平其实没怎么动,就抑制掉这条告警。

为什么要有第二道闸(这是从"每天假告警"里总结出来的):
    纯环比最大的问题是**上一小时本身可能就不正常**。凌晨 2 点做闪购冲到 80 单,
    3 点回落到常态的 30 单,环比就是 -62%,天天来一条假告警。报多了人就再也不看了 ——
    一个被忽略的告警系统,比没有告警系统更糟,因为它给人虚假的安全感。
    有了基线,拿"最近 7 天凌晨 3 点的水平(比如 28 单)"参照,30 单 = 正常,正确抑制。
    反过来,真断货时(比如常态 28 单,突然掉到 2 单)相对基线也是暴跌,不会被误杀。

    相关配置见 config.py: BASELINE_ENABLED / BASELINE_LOOKBACK_DAYS /
    BASELINE_MIN_SAMPLES / BASELINE_TOLERANCE。关掉就退回纯环比。

⚠️ 性能注意(大数据量下这里比 API 调用还慢):
    早期实现里 check_metric() 每比对一个 SKU 就 _load_state() + _save_state() 一次,
    10000 个 SKU 就是 10000 次全量 JSON 读写(每次都要把整个文件反序列化再写回),
    实测下来这一项的耗时经常超过拉数据本身,是"监控跑得很慢"的真正原因。
    现在改成: collect_anomalies() 一次 load、循环比对、最后一次 save。
    check_metric() 保留,仅用于单条调试,不要在批量循环里调用。

⚠️ 并发注意:
    state.json 的更新是"读-改-写"三步。单进程内靠"一次 load、一次 save"已经够快,
    但**进程之间**没有保护:如果定时任务重叠(上一轮没跑完,cron 又起了一个进程),
    两个进程都 load 到旧值再各自 save,后写的会覆盖先写的,上一轮的基准值就丢了,
    环比结果跟着失真。所以这里对"读-改-写"整体加了跨进程文件锁(state_lock)。
    数据量变大后,建议把 state.json 换成 Redis 或数据库(见 README 的容量章节)。
"""
import contextlib
import json
import logging
import os
import time
from typing import Dict, List, Optional, Tuple

import config
import history

log = logging.getLogger(__name__)

try:  # POSIX
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt


@contextlib.contextmanager
def state_lock(timeout: float = 30.0):
    """
    跨进程排他锁,保护 state.json 的"读-改-写"整段操作。

    拿不到锁会等待;超过 timeout 直接抛 TimeoutError —— 宁可这一轮快速失败并留下日志,
    也不要在状态可能不一致的情况下继续跑、把错误的基准值写进去。
    """
    lock_path = config.STATE_FILE + ".lock"
    fh = open(lock_path, "a+")
    try:
        if fcntl is None:
            # Windows: msvcrt 只能锁真实存在的字节,空文件要先写 1 个字节
            fh.seek(0, os.SEEK_END)
            if fh.tell() < 1:
                fh.write(" ")
                fh.flush()
            fh.seek(0)

        deadline = time.time() + timeout
        while True:
            try:
                if fcntl:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.time() >= deadline:
                    raise TimeoutError(
                        f"等待 state.json 锁超时({timeout}s),可能有另一个监控进程正在运行"
                    )
                time.sleep(0.1)
        yield
    finally:
        try:
            if fcntl:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            else:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        fh.close()


@contextlib.contextmanager
def state_update():
    """
    「加锁 → 读 → 交出 state 供修改 → 自动写回」的统一入口。

    给需要读-改-写的调用方用(比如 incident.py 的 LLM 冷却位点)。
    之前 incident.py 直接调 _load_state()/_save_state(),绕过了锁,
    导致同一个文件有加锁和不加锁两条写入路径,并发保护形同虚设。

    用法:
        with state_update() as state:
            state["_llm:amazon:stockout"] = str(time.time())
    """
    with state_lock():
        state = _load_state()
        yield state
        _save_state(state)


def _load_state() -> dict:
    if os.path.exists(config.STATE_FILE):
        with open(config.STATE_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                # 状态文件损坏时不要让整轮监控挂掉,备份后重新开始
                backup = config.STATE_FILE + ".broken"
                os.replace(config.STATE_FILE, backup)
                return {}
    return {}


def _save_state(state: dict) -> None:
    tmp = config.STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, config.STATE_FILE)  # 原子写入,避免写一半进程被杀导致状态损坏


def _compare(platform: str, key: str, current_value: float, previous: Optional[float],
             threshold: float, direction: str = "drop", min_previous: float = 0.0) -> Dict:
    """
    纯函数:只做比较,不碰文件。

    :param threshold: 变动比例(0.3 = 30%),不是百分比数字,别传 30。
    :param direction: "drop" 只看下跌 / "rise" 只看上涨 / "both" 双向。
    :param min_previous: 基线的绝对量下限。低于这个量不做环比 —— 否则"1 单 → 0 单"
                         就是 -100%,夜间低流量时段必然误报。

    关于 None:
        current_value 为 None 表示**这次没取到数**,不是"取到 0"。
        两者必须区分:0 是真实断货要告警,None 是数据缺失,当成 0 会制造假断货告警。
        以前这里直接做 `current_value - previous`,None 会抛 TypeError,
        把整个监控进程打挂(且 collect_incidents 在 try 之外,一条告警都发不出去)。

    关于 direction:
        以前只有 drop / both 两个分支,传 "rise" 会被静默吞掉 —— 不报错、也不告警,
        调用方以为配了涨价检测,实际上什么都不会发生。现在补齐,并对非法值显式告警。
    """
    result = {"anomaly": False, "message": ""}

    if current_value is None:
        # 数据缺失,不是指标下降:不告警,也不参与本轮比较
        return result

    if direction not in ("drop", "rise", "both"):
        log.warning("未知的 direction=%r(platform=%s key=%s),已按 drop 处理。"
                    "合法值: drop / rise / both", direction, platform, key)
        direction = "drop"

    if previous is not None and previous > 0 and previous >= min_previous:
        change_ratio = (current_value - previous) / previous
        if direction == "drop" and change_ratio <= -threshold:
            result["anomaly"] = True
            result["message"] = (
                f"[{platform}] {key} 从 {previous} 降至 {current_value}"
                f"(降幅 {abs(change_ratio) * 100:.1f}%,超过阈值 {threshold * 100:.0f}%)"
            )
        elif direction == "rise" and change_ratio >= threshold:
            result["anomaly"] = True
            result["message"] = (
                f"[{platform}] {key} 从 {previous} 涨至 {current_value}"
                f"(涨幅 {change_ratio * 100:.1f}%,超过阈值 {threshold * 100:.0f}%)"
            )
        elif direction == "both" and abs(change_ratio) >= threshold:
            result["anomaly"] = True
            result["message"] = (
                f"[{platform}] {key} 从 {previous} 变为 {current_value}"
                f"(变动 {change_ratio * 100:.1f}%,超过阈值 {threshold * 100:.0f}%)"
            )

    return result


def check_metric(platform: str, key: str, current_value: float, threshold: float,
                 direction: str = "drop") -> Dict:
    """
    单条指标检测(会读写 state.json)。**仅供调试**,
    批量场景请用 collect_anomalies(),否则每个 SKU 都会读写一次文件。
    """
    with state_lock():
        state = _load_state()
        state_key = f"{platform}:{key}"
        previous = state.get(state_key)
        result = _compare(platform, key, current_value, previous, threshold, direction)
        state[state_key] = current_value
        _save_state(state)
    return result


def _baseline_allows(platform: str, key: str, value: float, threshold: float,
                     direction: str, ts: Optional[float] = None) -> Tuple[bool, str]:
    """
    基线这道闸:环比异常,但相对"历史同时段的正常水平"其实是正常波动 → 抑制。

    这是解决误报的关键一环。环比(和上一小时比)最大的问题是**上一小时本身可能就不正常**:
    凌晨 2 点做个闪购冲到 80 单,3 点回落到常态的 30 单,环比就是 -62%,天天来一条假告警;
    但拿"最近 7 天凌晨 3 点的水平(比如 28 单)"当基线,30 单完全正常,正确抑制。

    :return: (是否放行, 说明)。放行=True 表示保留这条告警。
             样本不足 / 历史库不可用 / 基线为 0 时一律放行 ——
             **宁可多报,也不要因为数据不够而漏掉真异常**。
    """
    if not getattr(config, "BASELINE_ENABLED", False) or value is None:
        return True, ""
    try:
        base, n = history.baseline_with_fallback(platform, key, ts)
    except Exception as e:  # 历史库出任何问题都不能影响检测主流程
        log.warning("基线查询异常(%s),按环比判定", e)
        return True, ""

    if base is None:
        return True, f"基线样本不足(只有 {n} 条),按环比判定"
    if base <= 0:
        # 基线是 0 没法算变动比例(比如这个 SKU 历史上一直没库存),不抑制
        return True, ""

    ratio = (value - base) / base
    need = threshold * getattr(config, "BASELINE_TOLERANCE", 1.0)
    if direction == "drop":
        passed = ratio <= -need
    elif direction == "rise":
        passed = ratio >= need
    else:
        passed = abs(ratio) >= need

    if passed:
        return True, ""
    return False, (f"相对同时段基线 {base:g} 变动 {ratio * 100:+.1f}%,"
                   f"未达阈值 {need * 100:.0f}%")


def collect_incidents(checks: List[Dict], suppressed: Optional[List[Dict]] = None,
                      record_history: bool = True) -> List[Dict]:
    """
    批量异常检测:一次 load、循环比对、一次 save。

    返回结构化的异常列表(带 previous / change_ratio,供 incident.py 做分类与建议):
    [{"platform","key","value","previous","change_ratio","threshold","direction","message"}, ...]

    :param suppressed: 可选。传入一个列表,被基线抑制掉的项会写进去 ——
                       抑制了什么必须能看见,否则"告警变少了"到底是降噪成功
                       还是检测坏了,你根本分不清。
    :param record_history: 是否把本轮取值写入历史库。必须在**比对之后**写,
                           否则今天的值会混进基线里,等于自己跟自己比。
    """
    if not checks:
        return []

    now_ts = time.time()
    with state_lock():
        state = _load_state()
        incidents = []

        for c in checks:
            state_key = f"{c['platform']}:{c['key']}"
            previous = state.get(state_key)
            value = c["value"]
            direction = c.get("direction", "drop")
            r = _compare(
                c["platform"], c["key"], value, previous,
                c["threshold"], direction,
                c.get("min_previous", 0.0),
            )
            if r["anomaly"]:
                allowed, note = _baseline_allows(
                    c["platform"], c["key"], value, c["threshold"], direction, now_ts)
                if allowed:
                    ratio = (value - previous) / previous if previous else None
                    incidents.append({
                        "platform": c["platform"],
                        "key": c["key"],
                        "value": value,
                        "previous": previous,
                        "change_ratio": ratio,
                        "threshold": c["threshold"],
                        "direction": direction,
                        "message": r["message"],
                    })
                else:
                    log.info("[%s] %s 环比异常但已被基线抑制:%s(环比判定:%s)",
                             c["platform"], c["key"], note, r["message"])
                    if suppressed is not None:
                        suppressed.append({
                            "platform": c["platform"], "key": c["key"],
                            "value": value, "previous": previous,
                            "reason": note, "message": r["message"],
                        })
            # value 为 None 表示本轮没取到数,不能写进状态:
            # 否则会把上一轮的真实基线冲掉,下一轮永远比不出变化。
            if value is not None:
                state[state_key] = value

        _save_state(state)

    # 比对完之后才写历史:顺序反了会把本轮值混进基线,变成自己跟自己比,
    # 基线永远等于当前值 —— 于是所有告警都被"自己跟自己一样"抑制掉,监控彻底失明。
    if record_history:
        try:
            history.record_many([
                (c["platform"], c["key"], c["value"], now_ts)
                for c in checks if c.get("value") is not None
            ])
        except Exception as e:
            log.warning("写入指标历史失败(%s),不影响本轮检测", e)
    return incidents


def collect_anomalies(checks: List[Dict]) -> List[str]:
    """只要文案时用这个(兼容旧调用);需要分类/建议请用 collect_incidents()。"""
    return [i["message"] for i in collect_incidents(checks)]


# ---------------- 同步位点(用于增量拉取) ----------------
def get_sync_cursor(name: str) -> Optional[str]:
    """读取上次成功同步的时间点(ISO8601 字符串),没有则返回 None"""
    with state_lock():
        return _load_state().get(f"_cursor:{name}")


def set_sync_cursor(name: str, iso_ts: str) -> None:
    """记录本次成功同步的时间点,下一轮据此做增量拉取"""
    with state_lock():
        state = _load_state()
        state[f"_cursor:{name}"] = iso_ts
        _save_state(state)
