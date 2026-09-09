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
import threading
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
    dow      INTEGER,                -- 星期几(0=周一..6=周日)。可为 NULL:
                                     --   ALTER TABLE 加列时不能有 NOT NULL(无默认值),
                                     --   老库迁移期间先留空再回填。
    value    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mh_series ON metric_history(platform, key, ts);
CREATE INDEX IF NOT EXISTS idx_mh_hour   ON metric_history(platform, key, hour, ts);
"""

# dow 上的索引**不能**和建表放在同一段里:老库升级时 CREATE TABLE IF NOT EXISTS
# 会被跳过(表已存在),紧接着建这个索引就会因为 dow 列还不存在而报
# no such column: dow,整个建表流程失败 —— 结果是升级后历史库直接不可用。
# 所以它必须等迁移加完列之后再建。
_INDEX_DOW = ("CREATE INDEX IF NOT EXISTS idx_mh_dow "
              "ON metric_history(platform, key, dow, hour, ts)")

# 老库升级:加 dow 列并把历史行回填出来。
# 用 SQL 直接算,避免把几百万行读进 Python 再逐行 UPDATE。
#   SQLite 的 strftime('%w') 是 0=周日..6=周六,而 Python 的 weekday() 是 0=周一,
#   所以 (w + 6) % 7 把两边对齐 —— 这个偏移搞错的话,基线会系统性取错星期几,
#   而且完全不报错,只是周末的量被当成工作日的量来比。
_MIGRATE_DOW = """
UPDATE metric_history
   SET dow = (CAST(strftime('%w', ts, 'unixepoch', 'localtime') AS INTEGER) + 6) % 7
         WHERE dow IS NULL
"""

# 连接失败时只警告一次,别每轮刷屏
_unavailable_logged = False

# "开了只记重点 SKU 却没配 FOCUS_SKUS"也只警告一次
_focus_warned = False

# 按线程缓存的数据库连接(见 _connect 的注释:为什么必须复用)
_thread_local = threading.local()


def close() -> None:
    """关闭本线程缓存的连接。长期运行的进程(daemon)退出前可调用;一般不用管。"""
    conn = getattr(_thread_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
    _thread_local.conn = None
    _thread_local.db_path = None
    _thread_local.schema_ready = False


def _ensure_schema(conn: sqlite3.Connection) -> bool:
    """
    保证表已建。建过了就跳过 —— 复用连接后这条 SQL 每轮只跑一次,开销可忽略。

    为什么不能只在"新建连接"时建表:
        连接是复用的,而 write=False 打开的连接**故意不建表**(is_available() 要靠
        "表不存在"来判断这个库还没初始化过)。于是顺序一旦是"先读后写",
        复用的就是一个没建表的连接,写入时报 no such table。
        所以"表建好没"必须作为独立状态跟着连接走,而不是隐含在"新建"这个动作里。
    """
    if getattr(_thread_local, "schema_ready", False):
        return True
    try:
        conn.executescript(_SCHEMA)
        _migrate_add_dow(conn)   # 老库补列 —— 必须在建 dow 索引之前
        conn.execute(_INDEX_DOW)
        _thread_local.schema_ready = True
        return True
    except sqlite3.Error as e:
        log.warning("建表失败(%s)", e)
        close()
        return False


def _migrate_add_dow(conn: sqlite3.Connection) -> None:
    """
    给早先建的库补上 dow 列(那时还没有"区分工作日/周末"这个能力)。

    为什么不能用 CREATE TABLE IF NOT EXISTS 顺便解决:
        表已经存在时这条语句直接跳过,新列根本不会被加上。列的新增只能靠
        ALTER TABLE,而且必须显式判断列是否已存在 —— 否则每次启动都跑一遍迁移。
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(metric_history)")}
    if "dow" not in cols:
        conn.execute("ALTER TABLE metric_history ADD COLUMN dow INTEGER")
        conn.execute(_MIGRATE_DOW)
        conn.commit()
        log.info("指标历史库已升级:新增 dow 列并回填 %d 行", conn.total_changes)
    elif conn.execute(
            "SELECT 1 FROM metric_history WHERE dow IS NULL LIMIT 1").fetchone():
        # 列在但值没回填(上一轮迁移被打断):补一次
        conn.execute(_MIGRATE_DOW)
        conn.commit()


def _connect(write: bool = True) -> Optional[sqlite3.Connection]:
    """
    取一个可用连接。**连接按线程复用**,不要 close 它。

    为什么必须复用(这条是实测出来的,不是想当然):
        基线是**每个异常指标查一次**的。24 万行的库里,一次查询本身只要 0.015 ms,
        但"新建连接 + PRAGMA"要 ~10 ms —— 也就是说 99.9% 的时间花在反复开关连接上。
        10,000 个 SKU 同时异常时,光这一项就是 100 多秒。
        复用之后同样场景降到毫秒级,而代码只多了一个 thread-local 缓存。

    复用带来的两个坑,都已处理:
        1. **库路径变了不能复用**:测试里每个用例都换一个临时库,生产上 HISTORY_DB
           也可能被改。所以缓存时记下路径,路径不一致就关掉旧连接重建 ——
           否则会读写到上一个库,manifest 为"数据串了"且极难排查。
        2. **多线程**:sqlite3 连接默认不能跨线程。用 thread-local 各存各的,
           天然规避;监控是单线程,这里只是防止将来有人开多线程踩坑。

    任何异常都返回 None —— 历史是增强能力,不是核心链路,存不了就降级,绝不能拖垮监控。
    """
    global _unavailable_logged
    db_path = config.HISTORY_DB
    cached = getattr(_thread_local, "conn", None)
    if cached is not None and getattr(_thread_local, "db_path", None) == db_path:
        if write and not _ensure_schema(cached):
            return None
        return cached
    if cached is not None:
        close()  # 路径变了,旧连接必须关掉再建新的

    try:
        if write:
            os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=30.0)
        # WAL 是**写在数据库文件头里的持久属性**,设一次就永久生效,不必每次连接都设。
        # 实测每条指标查一次基线的场景下,重复执行这条 PRAGMA 占了绝大部分耗时
        # (它要动文件、要拿锁)。所以只在建表那次顺手设掉,读连接直接跳过。
        if write:
            # WAL:读写不互相阻塞。监控进程写的同时也能被 history 命令查。
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _thread_local.conn = conn
        _thread_local.db_path = db_path
        _thread_local.schema_ready = False
        if write and not _ensure_schema(conn):
            return None
        return conn
    except (sqlite3.Error, OSError) as e:
        # OSError 必须一起兜:os.makedirs 在"目标路径已存在同名文件"、
        # "父目录没权限"时抛的是 FileExistsError / PermissionError,不是 sqlite3.Error。
        # 只 catch sqlite3.Error 的话,这些情况下异常会一路抛到调用方 ——
        # 一个"存历史"的增强功能反而能把整个监控进程搞挂,属于典型的降级没做到底。
        close()
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
        pass  # 连接由 _connect 按线程复用,这里**不能** close


def record(platform: str, key: str, value: float, ts: Optional[float] = None) -> bool:
    """写一条历史。value 为 None 表示这轮没取到数,不记 —— 不污染基线。"""
    if value is None:
        return False
    return record_many([(platform, key, value, ts)])


def _focus_only_active() -> bool:
    """
    是否启用了"只给重点 SKU 存历史"。

    开了开关却没配 FOCUS_SKUS 是个很容易犯的配置错误 —— 结果是一条历史都不记,
    基线对比静默失效,而系统表面上一切正常。这里警告一次说清楚后果。
    """
    global _focus_warned
    if not getattr(config, "HISTORY_FOCUS_ONLY", False):
        return False
    if not getattr(config, "FOCUS_SKUS", None) and not _focus_warned:
        log.warning(
            "HISTORY_FOCUS_ONLY=True 但没有配置 FOCUS_SKUS,结果是一条历史都不记 —— "
            "基线对比会完全失效,行为退回纯环比(可能多报,不会漏报)。"
            "要么配上 FOCUS_SKUS,要么关掉这个开关。")
        _focus_warned = True
    return True


def _is_focus_key(key: str) -> bool:
    """key(形如 stock:SKU123)是否属于 FOCUS_SKUS 里的重点 SKU"""
    for sku in getattr(config, "FOCUS_SKUS", None) or []:
        if sku and sku in key:
            return True
    return False


def record_many(samples: Sequence[Tuple[str, str, float, Optional[float]]]) -> bool:
    """
    批量写历史(单事务)。

    :param samples: [(platform, key, value, ts|None), ...]
    """
    focus_only = _focus_only_active()
    rows = []
    for platform, key, value, ts in samples:
        if value is None:
            continue
        if focus_only and not _is_focus_key(key):
            # 长尾 SKU 不存历史。后果是它查不到基线 → 样本不足 → 一律放行,
            # 行为退回纯环比。这是**故意**的:宁可多报,不因省空间而漏报。
            continue
        ts = time.time() if ts is None else ts
        # weekday():0=周一..6=周日。与 _MIGRATE_DOW 里的口径保持一致。
        moment = datetime.datetime.fromtimestamp(ts)
        try:
            rows.append((platform, key, float(ts), moment.hour, moment.weekday(),
                         float(value)))
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
            "INSERT INTO metric_history (platform, key, ts, hour, dow, value) "
            "VALUES (?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        return True
    except sqlite3.Error as e:
        log.warning("写入指标历史失败(%s),本轮历史未记录", e)
        return False
    finally:
        pass  # 连接由 _connect 按线程复用,这里**不能** close


def baseline(platform: str, key: str, ts: Optional[float] = None,
             lookback_days: Optional[int] = None,
             min_samples: Optional[int] = None,
             hour_window: int = 0,
             match_dow: Optional[bool] = None) -> Tuple[Optional[float], int]:
    """
    取"历史上同一时段"的中位数作为基线。

    :param hour_window: 小时的容差。0 = 只要该小时本身;1 = 该小时 ±1。
    :param match_dow: 是否要求星期几也相同。None = 取配置 BASELINE_MATCH_DOW。
                      ⚠️ 开启后样本会少一大截(7 天回看里每个星期几只有 1 条),
                      所以必须配合 baseline_with_fallback() 的降级链 ——
                      单用这个函数很容易一直"样本不足",等于基线没开。
    :return: (基线值, 样本数)。样本不足或库不可用时返回 (None, 实际样本数)。

    为什么用中位数而不是平均数:一两次促销 / 断货会把平均值拉偏,
    中位数对这种离群值不敏感,更适合当"正常水平"的参照。
    """
    lookback_days = lookback_days if lookback_days is not None else config.BASELINE_LOOKBACK_DAYS
    min_samples = min_samples if min_samples is not None else config.BASELINE_MIN_SAMPLES
    if match_dow is None:
        match_dow = getattr(config, "BASELINE_MATCH_DOW", True)

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

        sql = (
            f"SELECT value FROM metric_history "
            # ts 用严格小于:本轮采集的值本身就是 now_ts,若用 <= 就会把自己算进基线,
            # 变成"自己跟自己比" —— 基线永远等于当前值,所有异常都被判为正常波动。
            f"WHERE platform=? AND key=? AND ts>=? AND ts<? AND hour IN ({placeholders})"
        )
        params = [platform, key, since, ts, *hours]
        if match_dow:
            sql += " AND dow=?"
            params.append(now.weekday())

        cur = conn.execute(sql, params)
        values = [r[0] for r in cur.fetchall()]
    except sqlite3.Error as e:
        log.warning("读取基线失败(%s),退回纯环比", e)
        return None, 0
    finally:
        pass  # 连接由 _connect 按线程复用,这里**不能** close

    if len(values) < min_samples:
        return None, len(values)
    return _median(values), len(values)


def baseline_with_fallback(platform: str, key: str, ts: Optional[float] = None
                           ) -> Tuple[Optional[float], int]:
    """
    从最严到最松依次放宽,取第一个样本够的口径。

        同星期几 + 同小时  →  同星期几 + 小时±1  →  不挑星期几 + 同小时
                                              →  不挑星期几 + 小时±1

    ⚠️ 这条降级链是**必需的**,不是锦上添花:
        区分星期几之后,7 天回看里每个星期几只剩 1 条样本,而 min_samples 默认 3 ——
        如果只取最严那一档,基线永远凑不够样本,结果就是基线等于没开、
        告警一条没少,而你以为已经降噪了。这种"开关开了但不生效"最坑人。
        所以必须一层层退到"能算出基线"为止,实在算不出才返回 None 让上层退回纯环比。

    数据充足时用最精确的口径(周末的量和周末比),新上的 SKU 数据少时靠放宽尽快攒够样本。
    """
    if getattr(config, "BASELINE_MATCH_DOW", True):
        b, n = baseline(platform, key, ts, hour_window=0, match_dow=True)
        if b is not None:
            return b, n
        b, n = baseline(platform, key, ts, hour_window=1, match_dow=True)
        if b is not None:
            return b, n

    b, n = baseline(platform, key, ts, hour_window=0, match_dow=False)
    if b is not None:
        return b, n
    return baseline(platform, key, ts, hour_window=1, match_dow=False)


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
        pass  # 连接由 _connect 按线程复用,这里**不能** close
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
        pass  # 连接由 _connect 按线程复用,这里**不能** close


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
        pass  # 连接由 _connect 按线程复用,这里**不能** close
