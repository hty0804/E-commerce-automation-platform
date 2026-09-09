"""
history.py —— 指标历史库(SQLite,append-only)。

为什么需要它:
    state.json 每个 key 只存"上一次的值",历史**全丢了**。这带来两个后果:
      1. 查不到历史 —— 上周三凌晨的订单量是多少?不知道。
      2. 做不了同时段对比 —— 只能和"上一小时"比(环比),而上一小时本身
         可能就不正常,这是误报的主要来源。

    举个真实例子:凌晨 2 点做闪购冲到 80 单,3 点回落到常态的 30 单,
    环比算出来是 -62%,于是每天这个点都来一条假告警。报多了人就再也不看告警了。
    有了历史就能拿"最近 7 天凌晨 3 点的水平(比如 28 单)"做基线,
    30 单 vs 28 单 = 正常,正确抑制。

为什么是 append-only:
    监控数据只追加、不修改。不做 UPDATE 就没有"改坏历史"的可能,
    出问题时历史本身是可信的证据。

为什么不把 state.json 整个迁过来:
    游标 / LLM 冷却 / 上架幂等这些状态已经在文件锁保护下并发安全了,
    迁库纯属增加风险、没有收益。这里只补"历史"这块真正缺的能力。

依赖:sqlite3(标准库)。不可用时全部函数优雅降级 ——
返回 None / 空列表,让检测逻辑退回纯环比,绝不因为存不了历史就让监控挂掉。
"""
import datetime
import logging
import os
import sqlite3
import time
from typing import Dict, List, Optional, Sequence, Tuple

import config

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS metric_history (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    key      TEXT NOT NULL,
    ts       REAL NOT NULL,          -- unix 时间戳(秒)
    hour     INTEGER NOT NULL,       -- 本地时间的小时(0-23),用于同时段对比
    value    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mh_series ON metric_history(platform, key, ts);
CREATE INDEX IF NOT EXISTS idx_mh_hour   ON metric_history(platform, key, hour, ts);
"""

# 连接失败时只警告一次,别每轮刷屏
_unavailable_logged = False


def _connect(write: bool = True) -> Optional[sqlite3.Connection]:
    """
    打开连接。任何异常都返回 None —— 历史是增强能力,不是核心链路,
    存不了就降级,绝不能拖垮监控。
    """
    global _unavailable_logged
    try:
        if write:
            os.makedirs(os.path.dirname(os.path.abspath(config.HISTORY_DB)) or ".", exist_ok=True)
        conn = sqlite3.connect(config.HISTORY_DB, timeout=30.0)
        # WAL 是**写在数据库文件头里的持久属性**,设一次就永久生效,不必每次连接都设。
        # 实测每条指标查一次基线的场景下,重复执行这条 PRAGMA 占了绝大部分耗时
        # (它要动文件、要拿锁)。所以只在建表那次顺手设掉,读连接直接跳过。
        if write:
            # WAL:读写不互相阻塞。监控进程写的同时也能被 history 命令查。
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        if write:
            conn.executescript(_SCHEMA)
        return conn
    except (sqlite3.Error, OSError) as e:
        # OSError 必须一起兜:os.makedirs 在"目标路径已存在同名文件"、
        # "父目录没权限"时抛的是 FileExistsError / PermissionError,不是 sqlite3.Error。
        # 只 catch sqlite3.Error 的话,这些情况下异常会一路抛到调用方 ——
        # 一个"存历史"的增强功能反而能把整个监控进程搞挂,属于典型的降级没做到底。
        if not _unavailable_logged:
            log.warning("指标历史库不可用(%s),已降级为不记录历史、不做基线对比。"
                        "检测逻辑不受影响,仍按环比工作。", e)
            _unavailable_logged = True
        return None


def is_available() -> bool:
    """历史库是否可用(供上层决定要不要走基线逻辑)"""
    conn = _connect(write=False)
    if conn is None:
        return False
    try:
        conn.execute("SELECT 1 FROM metric_history LIMIT 1")
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


def record(platform: str, key: str, value: float, ts: Optional[float] = None) -> bool:
    """写一条历史。value 为 None 表示这轮没取到数,不记 —— 不污染基线。"""
    if value is None:
        return False
    return record_many([(platform, key, value, ts)])


def record_many(samples: Sequence[Tuple[str, str, float, Optional[float]]]) -> bool:
    """
    批量写历史(单事务)。

    :param samples: [(platform, key, value, ts|None), ...]
    """
    rows = []
    for platform, key, value, ts in samples:
        if value is None:
            continue
        ts = time.time() if ts is None else ts
        hour = datetime.datetime.fromtimestamp(ts).hour
        try:
            rows.append((platform, key, float(ts), hour, float(value)))
        except (TypeError, ValueError):
            # 拿不到数字就不记,别让一条脏数据把整批写挂
            continue
    if not rows:
        return False

    conn = _connect()
    if conn is None:
        return False
    try:
        conn.executemany(
            "INSERT INTO metric_history (platform, key, ts, hour, value) VALUES (?,?,?,?,?)",
            rows,
        )
        conn.commit()
        return True
    except sqlite3.Error as e:
        log.warning("写入指标历史失败(%s),本轮历史未记录", e)
        return False
    finally:
        conn.close()


def baseline(platform: str, key: str, ts: Optional[float] = None,
             lookback_days: Optional[int] = None,
             min_samples: Optional[int] = None,
             hour_window: int = 0) -> Tuple[Optional[float], int]:
    """
    取"历史上同一时段"的中位数作为基线。

    :param hour_window: 小时的容差。0 = 只要该小时本身;1 = 该小时 ±1。
    :return: (基线值, 样本数)。样本不足或库不可用时返回 (None, 实际样本数)。

    为什么用中位数而不是平均数:一两次促销 / 断货会把平均值拉偏,
    中位数对这种离群值不敏感,更适合当"正常水平"的参照。
    """
    lookback_days = lookback_days if lookback_days is not None else config.BASELINE_LOOKBACK_DAYS
    min_samples = min_samples if min_samples is not None else config.BASELINE_MIN_SAMPLES

    conn = _connect(write=False)
    if conn is None:
        return None, 0
    try:
        ts = time.time() if ts is None else ts
        now = datetime.datetime.fromtimestamp(ts)
        # 减 1 秒留边界余量:datetime ↔ timestamp 之间有一次浮点往返,
        # 恰好落在 lookback 边界上的样本可能被算成"早了 0.0000001 秒"而被排除,
        # 白丢一条样本。样本本来就金贵(不够就做不了基线),丢不起。
        since = (now - datetime.timedelta(days=lookback_days)).timestamp() - 1.0
        hours = [(now.hour + d) % 24 for d in range(-hour_window, hour_window + 1)]
        placeholders = ",".join("?" * len(hours))

        cur = conn.execute(
            f"SELECT value FROM metric_history "
            # ts 用严格小于:本轮采集的值本身就是 now_ts,若用 <= 就会把自己算进基线,
            # 变成"自己跟自己比" —— 基线永远等于当前值,所有异常都被判为正常波动。
            f"WHERE platform=? AND key=? AND ts>=? AND ts<? AND hour IN ({placeholders})",
            [platform, key, since, ts, *hours],
        )
        values = [r[0] for r in cur.fetchall()]
    except sqlite3.Error as e:
        log.warning("读取基线失败(%s),退回纯环比", e)
        return None, 0
    finally:
        conn.close()

    if len(values) < min_samples:
        return None, len(values)
    return _median(values), len(values)


def baseline_with_fallback(platform: str, key: str, ts: Optional[float] = None
                           ) -> Tuple[Optional[float], int]:
    """
    先按"同一小时"取;样本不够就放宽到 ±1 小时。

    数据充足时用更精确的窗口,新上的 SKU 数据少时靠放宽窗口尽快攒够样本 ——
    但样本仍然不够就老实返回 None,让上层退回纯环比。
    """
    b, n = baseline(platform, key, ts, hour_window=0)
    if b is not None:
        return b, n
    return baseline(platform, key, ts, hour_window=1)


def _median(values: Sequence[float]) -> float:
    s = sorted(values)
    m = len(s) // 2
    if len(s) % 2:
        return float(s[m])
    return (s[m - 1] + s[m]) / 2.0


def recent(platform: str, key: str, limit: int = 50) -> List[Tuple[float, float]]:
    """最近 N 条历史,[(ts, value), ...],按时间正序"""
    conn = _connect(write=False)
    if conn is None:
        return []
    try:
        cur = conn.execute(
            "SELECT ts, value FROM metric_history WHERE platform=? AND key=? "
            "ORDER BY ts DESC LIMIT ?", [platform, key, limit])
        rows = cur.fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return list(reversed(rows))


def series() -> List[Tuple[str, str, int, Optional[float], Optional[float]]]:
    """列出所有指标序列:(platform, key, 样本数, 最早时间, 最新时间)"""
    conn = _connect(write=False)
    if conn is None:
        return []
    try:
        cur = conn.execute(
            "SELECT platform, key, COUNT(*), MIN(ts), MAX(ts) "
            "FROM metric_history GROUP BY platform, key ORDER BY platform, key")
        return [tuple(r) for r in cur.fetchall()]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def prune(retention_days: Optional[int] = None) -> int:
    """清理超过保留期的历史,返回删除行数"""
    retention_days = retention_days if retention_days is not None else config.HISTORY_RETENTION_DAYS
    conn = _connect(write=False)
    if conn is None:
        return 0
    try:
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=retention_days)).timestamp()
        cur = conn.execute("DELETE FROM metric_history WHERE ts < ?", [cutoff])
        conn.commit()
        deleted = cur.rowcount or 0
        if deleted:
            # 删完顺手回收空间,否则 db 文件只增不减
            conn.execute("VACUUM")
        return deleted
    except sqlite3.Error as e:
        log.warning("清理历史失败(%s)", e)
        return 0
    finally:
        conn.close()
