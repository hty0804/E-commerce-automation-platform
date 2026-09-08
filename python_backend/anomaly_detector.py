"""
简单的环比 + 阈值异常检测。

思路: 每次抓到关键指标(库存、订单量等)后存入本地 state.json,
下次运行时和上一次的数值做比较,变化幅度超过设定阈值就判定为异常。

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
import os
import time
from typing import Dict, List, Optional

import config

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
             threshold: float, direction: str = "drop") -> Dict:
    """纯函数:只做比较,不碰文件。"""
    result = {"anomaly": False, "message": ""}

    if previous is not None and previous > 0:
        change_ratio = (current_value - previous) / previous
        if direction == "drop" and change_ratio <= -threshold:
            result["anomaly"] = True
            result["message"] = (
                f"[{platform}] {key} 从 {previous} 降至 {current_value}"
                f"(降幅 {abs(change_ratio) * 100:.1f}%,超过阈值 {threshold * 100:.0f}%)"
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


def collect_incidents(checks: List[Dict]) -> List[Dict]:
    """
    批量异常检测:一次 load、循环比对、一次 save。

    返回结构化的异常列表(带 previous / change_ratio,供 incident.py 做分类与建议):
    [{"platform","key","value","previous","change_ratio","threshold","direction","message"}, ...]
    """
    if not checks:
        return []

    with state_lock():
        state = _load_state()
        incidents = []

        for c in checks:
            state_key = f"{c['platform']}:{c['key']}"
            previous = state.get(state_key)
            value = c["value"]
            r = _compare(
                c["platform"], c["key"], value, previous,
                c["threshold"], c.get("direction", "drop"),
            )
            if r["anomaly"]:
                ratio = (value - previous) / previous if previous else None
                incidents.append({
                    "platform": c["platform"],
                    "key": c["key"],
                    "value": value,
                    "previous": previous,
                    "change_ratio": ratio,
                    "threshold": c["threshold"],
                    "direction": c.get("direction", "drop"),
                    "message": r["message"],
                })
            state[state_key] = value

        _save_state(state)
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
