"""
指标历史库 + 基线对比的回归测试。

运行:
    python -m unittest discover -s python_backend/tests

这里锁住的是一类很容易被"看起来合理"糊过去的 bug:
告警到底有没有被抑制、抑制得对不对,光看"群里安静了"是分不出来的。
所以每个抑制类断言都配一个**反向验证** —— 关掉基线,同一份数据必须重新出告警。
只有正反两个方向都对,才能证明基线真的在工作,而不是检测逻辑整个坏掉了。
"""
import datetime
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="ecom_sop_hist_")
os.environ.setdefault("STATE_FILE", os.path.join(_TMP, "state.json"))
os.environ.setdefault("HISTORY_DB", os.path.join(_TMP, "history.db"))

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import anomaly_detector  # noqa: E402
import config  # noqa: E402
import history  # noqa: E402

DAY = 86400.0


def _same_hour_ts(days_ago: int, base: float = None) -> float:
    """取 N 天前的同一时刻(保证 hour 相同,落在同一个同时段窗口里)"""
    base = time.time() if base is None else base
    return base - days_ago * DAY


class _HistoryCase(unittest.TestCase):
    """每个用例一套独立的 state.json / history.db,避免互相污染"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ecom_hist_case_")
        self.state_file = os.path.join(self.tmp, "state.json")
        self.db = os.path.join(self.tmp, "history.db")
        self._p = [
            mock.patch.object(config, "STATE_FILE", self.state_file),
            mock.patch.object(config, "HISTORY_DB", self.db),
            mock.patch.object(config, "BASELINE_ENABLED", True),
            mock.patch.object(config, "BASELINE_MIN_SAMPLES", 3),
            mock.patch.object(config, "BASELINE_LOOKBACK_DAYS", 7),
            mock.patch.object(config, "BASELINE_MATCH_DOW", True),
            mock.patch.object(config, "BASELINE_TOLERANCE", 1.0),
        ]
        for p in self._p:
            p.start()

    def tearDown(self):
        # 先关连接再撤 patch:连接是按线程缓存的,不关的话解释器退出时
        # 会报 ResourceWarning: unclosed database(每个用例一个临时库,很容易攒一堆)。
        history.close()
        for p in self._p:
            p.stop()

    def _set_state(self, mapping: dict) -> None:
        """
        显式设定"上一轮的值"。

        不能靠"先跑一轮 collect_incidents 把值写进 state"来铺垫 ——
        那一次调用同样会写历史、同样会推进状态,等到第二次调用时
        previous 已经是刚才那个值了,等于自己跟自己比,永远比不出变化。
        想对同一份数据做正反两次验证,就必须每次先把 state 摆回同一个起点。
        """
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(mapping, f)


class TestHistoryStore(_HistoryCase):
    def test_record_and_recent(self):
        now = time.time()
        history.record("amazon", "stock:S1", 30, ts=now)
        history.record("amazon", "stock:S1", 28, ts=now - 3600)
        rows = history.recent("amazon", "stock:S1")
        self.assertEqual([v for _, v in rows], [28, 30], "recent 应按时正序返回")

    def test_record_many_skips_none(self):
        """None = 本轮没取到数,绝不能进历史,否则会污染基线"""
        self.assertTrue(history.record_many([
            ("amazon", "stock:S1", 30, time.time()),
            ("amazon", "stock:S1", None, time.time()),
        ]))
        self.assertEqual(len(history.recent("amazon", "stock:S1")), 1)

    def test_median(self):
        self.assertEqual(history._median([3, 1, 2]), 2)
        self.assertEqual(history._median([4, 1, 2, 3]), 2.5)
        self.assertEqual(history._median([5]), 5)

    def test_baseline_is_median_of_same_hour(self):
        """
        用中位数而不是平均数:7 天里有 1 天闪购冲到 200 单,
        平均数会被拉到 50 多,中位数仍然贴近真实的常态 30。
        """
        now = time.time()
        samples = [30, 31, 29, 200, 28, 30, 32]  # 一天离群
        for i, v in enumerate(samples, start=1):
            history.record("amazon", "orders", v, ts=_same_hour_ts(i, now))
        base, n = history.baseline("amazon", "orders", ts=now, match_dow=False)
        self.assertEqual(n, 7)
        self.assertEqual(base, 30, "中位数应为 30,不受那次闪购影响")

    def test_samples_at_current_ts_are_excluded(self):
        """
        本轮时刻的样本必须排除在基线之外(严格 ts < now)。

        这是"先比对再写历史"之外的第二道保险:本轮采集的值带的正是 now_ts,
        上界一旦写成 <=,它就会把自己算进基线 → 自己跟自己比 → 基线恒等于当前值 →
        所有异常都被判为正常波动,监控彻底失明。
        样本数故意卡在门槛下一格,这样多算一条立刻从 None 变成有值。
        """
        now = time.time()
        for i in range(1, config.BASELINE_MIN_SAMPLES):  # 差一条够门槛
            history.record("amazon", "orders", 30, ts=_same_hour_ts(i, now))
        history.record("amazon", "orders", 999, ts=now)  # 恰好落在本轮时刻

        base, n = history.baseline("amazon", "orders", ts=now, match_dow=False)
        self.assertIsNone(base, "本轮时刻的样本不应参与本轮基线")
        self.assertEqual(n, config.BASELINE_MIN_SAMPLES - 1)

    def test_baseline_ignores_other_hours(self):
        """同时段的意义就在这:别的小时的数据不能混进来"""
        now = time.time()
        for i in range(1, 6):
            history.record("amazon", "orders", 900, ts=_same_hour_ts(i, now) - 6 * 3600)
        for i in range(1, 6):
            history.record("amazon", "orders", 30, ts=_same_hour_ts(i, now))
        base, n = history.baseline("amazon", "orders", ts=now, match_dow=False)
        self.assertEqual(n, 5)
        self.assertEqual(base, 30)

    def test_baseline_with_fallback_widens_window(self):
        """样本不够时放宽到 ±1 小时,而不是直接放弃"""
        now = time.time()
        for i in range(1, 4):
            history.record("amazon", "orders", 30, ts=_same_hour_ts(i, now) - 3600)
        base, n = history.baseline("amazon", "orders", ts=now,
                                  hour_window=0, match_dow=False)
        self.assertIsNone(base, "严格同一小时时样本应为 0")
        base2, n2 = history.baseline_with_fallback("amazon", "orders", ts=now)
        self.assertEqual(base2, 30)

    def test_prune_removes_old_rows(self):
        now = time.time()
        history.record("amazon", "orders", 10, ts=now - 400 * DAY)
        history.record("amazon", "orders", 20, ts=now)
        deleted = history.prune(retention_days=90)
        self.assertEqual(deleted, 1)
        self.assertEqual([v for _, v in history.recent("amazon", "orders")], [20])

    def test_connection_not_reused_across_db_paths(self):
        """
        库路径变了必须换连接。

        连接是按线程复用的,如果不管路径直接复用,切库后会读写到**上一个库** ——
        测试里表现为用例之间数据串了,生产里表现为"A 环境的基线跑到 B 环境去了"。
        这种串数据的 bug 没有报错、只有结果不对,极难排查,所以必须钉死。
        """
        history.record("amazon", "k", 1)
        self.assertEqual([v for _, v in history.recent("amazon", "k")], [1])

        other_db = os.path.join(self.tmp, "second.db")
        with mock.patch.object(config, "HISTORY_DB", other_db):
            self.assertEqual(history.recent("amazon", "k"), [],
                             "换库后不该看到旧库的数据")
            history.record("amazon", "k", 2)
            self.assertEqual([v for _, v in history.recent("amazon", "k")], [2])

        # 切回原库,原来的数据还在,且没被第二个库污染
        self.assertEqual([v for _, v in history.recent("amazon", "k")], [1])

    def test_usable_from_another_thread(self):
        """
        别的线程里也要能用。

        sqlite3 连接默认禁止跨线程使用(check_same_thread),一用就抛
        ProgrammingError。连接缓存用 thread-local 正是为了规避这点 ——
        但那只是"应该没事",这里真的开个线程跑一遍才算数。
        """
        outcome = []

        def worker():
            try:
                history.record("amazon", "k", 7)
                outcome.append([v for _, v in history.recent("amazon", "k")])
            except Exception as e:  # noqa: BLE001
                outcome.append(e)

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=10)
        self.assertEqual(outcome, [[7]], f"子线程里应能正常读写,实际: {outcome}")

    def test_unavailable_degrades_gracefully(self):
        """
        历史库打不开时:所有函数不得抛异常,监控要继续按环比工作。
        存储是增强能力,不能反过来成为核心链路的单点故障。
        """
        blocker = os.path.join(self.tmp, "not_a_dir")
        with open(blocker, "w") as f:
            f.write("x")
        bad_db = os.path.join(blocker, "history.db")
        with mock.patch.object(config, "HISTORY_DB", bad_db):
            self.assertFalse(history.is_available())
            self.assertFalse(history.record("amazon", "orders", 1))
            self.assertEqual(history.recent("amazon", "orders"), [])
            self.assertEqual(history.baseline("amazon", "orders"), (None, 0))
            self.assertEqual(history.prune(), 0)


class TestDayOfWeek(_HistoryCase):
    """
    区分工作日 / 周末。

    跨境电商周末和工作日的订单节奏差别很大,拿工作日的量去衡量周末会系统性误判。
    但这里有个陷阱:区分星期几之后样本会少一大截(7 天回看里每个星期几只有 1 条),
    所以"降级链"才是这个功能能不能真正生效的关键。
    """

    def _seed_same_weekday(self, key, values, base=None):
        """按周铺样本:每次减 7 天,保证星期几相同、小时相同"""
        base = time.time() if base is None else base
        for i, v in enumerate(values, start=1):
            history.record("amazon", key, v, ts=base - i * 7 * DAY)

    def test_prefers_same_weekday(self):
        """
        周末的量应该跟周末比。这里工作日是 100、周末是 20,
        在周末查询时基线必须是 20,不能是混合后的中位数。
        """
        now = time.time()
        # 同星期几(减 7/14/21 天)是 20;其余日子是 100
        self._seed_same_weekday("orders", [20, 21, 19], base=now)
        for d in (1, 2, 3, 4, 5, 6):
            if d % 7 == 0:
                continue
            history.record("amazon", "orders", 100, ts=now - d * DAY)

        base, n = history.baseline("amazon", "orders", ts=now,
                                   lookback_days=28, match_dow=True)
        self.assertEqual(n, 3)
        self.assertEqual(base, 20, "同星期几的样本才该进基线")

    def test_seven_day_weekday_window_is_insufficient(self):
        """7 天回看只会命中上一周同一天,不足 min_samples 时必须放行。"""
        now = time.time()
        history.record("amazon", "orders", 20, ts=now - 7 * DAY)
        base, n = history.baseline("amazon", "orders", ts=now,
                                   lookback_days=7, match_dow=True)
        self.assertIsNone(base)
        self.assertEqual(n, 1)

    def test_falls_back_when_same_weekday_samples_insufficient(self):
        """
        降级链(最关键的一条):同星期几样本不够时,必须退到"不挑星期几"
        把基线算出来,而不是直接放弃。

        如果少了这一层,7 天回看 + min_samples=3 的组合下基线永远凑不够,
        表现就是"开关开了但告警一条没少",而你以为已经降噪了 —— 最坑人的一种失效。
        """
        now = time.time()
        for d in (1, 2, 3, 4):  # 都是不同星期几
            history.record("amazon", "orders", 50, ts=now - d * DAY)

        # 严格同星期几:样本 0,算不出
        strict, n_strict = history.baseline("amazon", "orders", ts=now,
                                            match_dow=True)
        self.assertIsNone(strict)
        # 但走 fallback 链必须能算出来
        base, n = history.baseline_with_fallback("amazon", "orders", ts=now)
        self.assertEqual(base, 50, "样本不够时应降级到不挑星期几")
        self.assertGreaterEqual(n, 3)

    def test_migration_backfills_dow_for_old_rows(self):
        """
        老库升级:早先写入的行没有 dow,迁移必须把它们回填出来。

        不回填的话这些行在 match_dow=True 时永远命中不了 —— 表现为
        "升级后基线突然不抑制了",而且没人会想到是迁移漏了。
        """
        import sqlite3

        conn = sqlite3.connect(self.db)
        conn.executescript(
            "CREATE TABLE metric_history ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL,"
            " key TEXT NOT NULL, ts REAL NOT NULL, hour INTEGER NOT NULL,"
            " value REAL NOT NULL)")
        old_ts = time.time() - 3 * DAY  # 3 天前,星期几与今天不同
        conn.execute(
            "INSERT INTO metric_history (platform,key,ts,hour,value) VALUES (?,?,?,?,?)",
            ("amazon", "orders", old_ts,
             datetime.datetime.fromtimestamp(old_ts).hour, 42.0))
        conn.commit()
        conn.close()

        # 新代码连上来:应自动加列并回填
        history.record("amazon", "orders", 1)  # 触发 _ensure_schema
        conn = sqlite3.connect(self.db)
        rows = conn.execute("SELECT ts, dow FROM metric_history").fetchall()
        conn.close()
        self.assertEqual(len(rows), 2)
        for ts, dow in rows:
            self.assertIsNotNone(dow, "迁移后 dow 不应为 NULL")
            self.assertEqual(dow, datetime.datetime.fromtimestamp(ts).weekday(),
                             "回填的星期几必须和 Python 口径一致(0=周一)")


class TestFocusOnly(_HistoryCase):
    """
    只给重点 SKU 存历史(HISTORY_FOCUS_ONLY)。

    省空间的代价是长尾 SKU 查不到基线,所以这里最要紧的是验证**降级方向是对的**:
    没有基线时必须放行(退回纯环比、可能多报),绝不能反过来把告警吃掉。
    """

    def setUp(self):
        super().setUp()
        # _focus_warned 是模块级的"只警告一次"标志,用例之间要清掉,
        # 否则只有第一个用例能看到警告,后面的断言会不稳定。
        history._focus_warned = False

    def test_all_recorded_when_disabled(self):
        now = time.time()
        with mock.patch.object(config, "HISTORY_FOCUS_ONLY", False), \
             mock.patch.object(config, "FOCUS_SKUS", ["SKU1"]):
            history.record_many([("amazon", "stock:SKU1", 1, now),
                                 ("amazon", "stock:SKU2", 2, now)])
        self.assertEqual(len(history.recent("amazon", "stock:SKU1")), 1)
        self.assertEqual(len(history.recent("amazon", "stock:SKU2")), 1)

    def test_only_focus_recorded_when_enabled(self):
        now = time.time()
        with mock.patch.object(config, "HISTORY_FOCUS_ONLY", True), \
             mock.patch.object(config, "FOCUS_SKUS", ["SKU1"]):
            history.record_many([("amazon", "stock:SKU1", 1, now),
                                 ("amazon", "stock:SKU2", 2, now)])
        self.assertEqual(len(history.recent("amazon", "stock:SKU1")), 1)
        self.assertEqual(len(history.recent("amazon", "stock:SKU2")), 0,
                         "非重点 SKU 不该存历史")

    def test_enabled_without_focus_list_warns(self):
        """
        开了开关却没配 FOCUS_SKUS = 一条历史都不记,基线静默失效。
        这种"配置错了但系统看起来正常"的情况必须有明确警告。
        """
        with mock.patch.object(config, "HISTORY_FOCUS_ONLY", True), \
             mock.patch.object(config, "FOCUS_SKUS", []):
            with self.assertLogs(history.log, level="WARNING") as cm:
                history.record("amazon", "stock:SKU1", 1)
        self.assertTrue(any("FOCUS_SKUS" in m for m in cm.output))
        # 一条都没记下去
        self.assertEqual(history.recent("amazon", "stock:SKU1"), [])

    def test_long_tail_sku_still_alerts(self):
        """
        安全降级的核心:长尾 SKU 没有历史 → 样本不足 → 一律放行。
        省了空间,但不能因此漏报 —— 这条是最要紧的断言。
        """
        now = time.time()
        # 只给重点 SKU 灌历史
        with mock.patch.object(config, "HISTORY_FOCUS_ONLY", False):
            for i in range(1, 6):
                history.record("amazon", "stock:SKU1", 30, ts=_same_hour_ts(i, now))

        with mock.patch.object(config, "HISTORY_FOCUS_ONLY", True), \
             mock.patch.object(config, "FOCUS_SKUS", ["SKU1"]):
            # 重点 SKU:回到常态 30,应被基线抑制
            self._set_state({"amazon:stock:SKU1": 80})
            inc_focus = anomaly_detector.collect_incidents(
                [{"platform": "amazon", "key": "stock:SKU1", "value": 30,
                  "threshold": 0.3, "direction": "drop"}])
            # 长尾 SKU:同样回到常态 30,但没有基线 → 必须放行
            self._set_state({"amazon:stock:SKU2": 80})
            inc_tail = anomaly_detector.collect_incidents(
                [{"platform": "amazon", "key": "stock:SKU2", "value": 30,
                  "threshold": 0.3, "direction": "drop"}])

        self.assertEqual(len(inc_focus), 0, "重点 SKU 应被基线抑制")
        self.assertEqual(len(inc_tail), 1,
                         "长尾 SKU 没有基线,必须放行 —— 省空间不能变成漏报")


class TestBaselineSuppression(_HistoryCase):
    """基线这道闸:该抑制的抑制,该报的一个都不能漏"""

    def _seed_normal(self, platform, key, values=(28, 30, 29, 31, 30)):
        now = time.time()
        for i, v in enumerate(values, start=1):
            history.record(platform, key, v, ts=_same_hour_ts(i, now))

    def test_flash_sale_rebound_is_suppressed(self):
        """
        核心场景:凌晨闪购冲到 80 单,回落到常态 30 单。
        环比 -62%,但相对基线 30 完全正常 —— 必须抑制。
        """
        self._seed_normal("amazon", "orders")
        self._set_state({"amazon:orders": 80})  # 上一轮:闪购冲到 80 单

        suppressed = []
        inc = anomaly_detector.collect_incidents(
            [{"platform": "amazon", "key": "orders", "value": 30,
              "threshold": 0.3, "direction": "drop"}],
            suppressed=suppressed)
        self.assertEqual(len(inc), 0, "闪购后回落到常态 30 不该告警(基线应抑制)")
        self.assertEqual(len(suppressed), 1)
        self.assertIn("基线", suppressed[0]["reason"])

    def test_reverse_verify_suppression_is_from_baseline(self):
        """
        反向验证:同一份数据关掉基线,告警必须重新出现。
        否则"没有告警"可能只是检测逻辑坏了,而不是降噪成功。
        """
        self._seed_normal("amazon", "orders")
        checks = [{"platform": "amazon", "key": "orders", "value": 30,
                   "threshold": 0.3, "direction": "drop"}]

        # 两次判定的起点必须完全一致,否则比的是"基线开关"以外的东西
        self._set_state({"amazon:orders": 80})
        with mock.patch.object(config, "BASELINE_ENABLED", True):
            on = anomaly_detector.collect_incidents(list(checks))
        self._set_state({"amazon:orders": 80})
        with mock.patch.object(config, "BASELINE_ENABLED", False):
            off = anomaly_detector.collect_incidents(list(checks))

        self.assertEqual(len(on), 0, "基线开启时应被抑制")
        self.assertEqual(len(off), 1, "基线关闭时必须重新告警 —— 否则基线是死代码")

    def test_real_stockout_still_fires(self):
        """真断货:常态 30,现在 2。相对基线也是暴跌,绝不能被误杀"""
        self._seed_normal("amazon", "stock:S1")
        self._set_state({"amazon:stock:S1": 30})
        suppressed = []
        inc = anomaly_detector.collect_incidents(
            [{"platform": "amazon", "key": "stock:S1", "value": 2,
              "threshold": 0.3, "direction": "drop"}],
            suppressed=suppressed)
        self.assertEqual(len(inc), 1, "真断货必须告警")
        self.assertEqual(suppressed, [])

    def test_insufficient_samples_never_suppress(self):
        """
        样本不足一律放行 —— 宁可多报,也不能因为数据不够漏掉真异常。
        """
        now = time.time()
        history.record("amazon", "orders", 30, ts=_same_hour_ts(1, now))  # 只有 1 条
        self._set_state({"amazon:orders": 100})
        inc = anomaly_detector.collect_incidents(
            [{"platform": "amazon", "key": "orders", "value": 10,
              "threshold": 0.3, "direction": "drop"}])
        self.assertEqual(len(inc), 1, "样本不足时应按环比放行")

    def test_rise_direction_also_gated(self):
        """涨价这道闸同样要过基线,不能只管跌"""
        self._seed_normal("amazon", "price:S1")
        self._set_state({"amazon:price:S1": 19.99})
        inc = anomaly_detector.collect_incidents(
            [{"platform": "amazon", "key": "price:S1", "value": 20.5,
              "threshold": 0.2, "direction": "rise"}])
        self.assertEqual(len(inc), 0, "相对基线只涨了 2.5%,应被抑制")

    def test_this_round_value_not_in_its_own_baseline(self):
        """
        顺序不能反:先比对再写历史。反了就会把本轮值混进基线,变成自己跟自己比,
        基线永远等于当前值 —— 于是所有告警都被"自己跟自己一样"抑制掉,监控彻底失明。

        构造方式很讲究:历史铺到 min_samples-1 条(差一条够门槛),
        本轮值取 31 —— 紧贴基线 30。
          - 顺序正确:查基线时只有 2 条样本 → 不足 → 放行 → 有告警。
          - 顺序反了:本轮的 31 先入库,样本凑够 3 条、基线 = 30,
            于是 31 vs 30 被判定为正常波动 → 抑制 → 没告警。
        两种实现的最终结果不同,断言才抓得住。
        (注意别在 collect 之后再去查基线 —— 那时本轮值早入库了,怎么查都一样。)
        """
        key = "stock:ORDER"
        now = time.time()
        for i in range(1, config.BASELINE_MIN_SAMPLES):
            history.record("amazon", key, 30, ts=_same_hour_ts(i, now))
        self._set_state({f"amazon:{key}": 1000})  # 上一轮 1000,本轮暴跌

        inc = anomaly_detector.collect_incidents(
            [{"platform": "amazon", "key": key, "value": 31,
              "threshold": 0.3, "direction": "drop"}])
        self.assertEqual(
            len(inc), 1,
            "样本不足(2 < 3)应放行;若本轮值被提前写进历史凑够样本,就会被自己抑制掉")
        # 历史里确实记下了这一轮,下一轮起可用
        self.assertEqual([v for _, v in history.recent("amazon", key)][-1], 31)

    def test_baseline_read_happens_before_history_write(self):
        """
        直接钉死调用顺序,不依赖样本数这类间接信号。

        上面的用例靠"样本数不够"来证明本轮值没被读进去;这条是正面记录
        read / write 的先后顺序。两条一起,任何一侧被绕过都会被抓到。
        """
        order = []
        real_baseline = history.baseline_with_fallback
        real_write = history.record_many

        with mock.patch.object(history, "baseline_with_fallback",
                               side_effect=lambda *a, **k: (order.append("read"),
                                                            real_baseline(*a, **k))[1]), \
             mock.patch.object(history, "record_many",
                               side_effect=lambda *a, **k: (order.append("write"),
                                                            real_write(*a, **k))[1]):
            self._set_state({"amazon:orders": 100})
            anomaly_detector.collect_incidents(
                [{"platform": "amazon", "key": "orders", "value": 10,
                  "threshold": 0.3, "direction": "drop"}])

        self.assertEqual(order, ["read", "write"],
                         "必须先查基线再写历史;反了就是自己跟自己比")


class TestShopIsolation(_HistoryCase):
    """多店铺(防关联多账号):指标历史必须按 shop_id 隔离。"""

    def test_records_are_invisible_across_shops(self):
        history.record("amazon", "orders", 30, shop_id="shop_a")
        history.record("amazon", "orders", 30, shop_id="shop_b")
        self.assertEqual(len(history.recent("amazon", "orders", shop_id="shop_a")), 1)
        self.assertEqual(len(history.recent("amazon", "orders", shop_id="shop_b")), 1)
        self.assertEqual(history.recent("amazon", "orders", shop_id="shop_c"), [])

    def test_baseline_does_not_borrow_other_shop_samples(self):
        """
        A 店攒够样本、B 店一条没有 → B 店必须判"样本不足",不能拿 A 店的量顶上。
        否则 B 店真跌了会被 A 店抬高的基线抑制掉 —— 静默漏报,而且看群里"很安静"。
        """
        now = time.time()
        for i in range(1, 8):
            history.record("amazon", "orders", 100, ts=_same_hour_ts(i, now), shop_id="shop_a")
        base_a, n_a = history.baseline("amazon", "orders", ts=now,
                                       match_dow=False, shop_id="shop_a")
        self.assertIsNotNone(base_a)
        self.assertEqual(n_a, 7)

        base_b, n_b = history.baseline("amazon", "orders", ts=now,
                                       match_dow=False, shop_id="shop_b")
        self.assertIsNone(base_b, "B 店不该看到 A 店的历史")
        self.assertEqual(n_b, 0)

    def test_series_is_scoped(self):
        history.record("amazon", "orders", 1, shop_id="shop_a")
        history.record("amazon", "stock:S1", 2, shop_id="shop_b")
        keys_a = [row[1] for row in history.series(shop_id="shop_a")]
        keys_b = [row[1] for row in history.series(shop_id="shop_b")]
        self.assertEqual(keys_a, ["orders"])
        self.assertEqual(keys_b, ["stock:S1"])

    def test_default_shop_follows_config(self):
        with mock.patch.object(config, "SHOP_ID", "shop_cfg"):
            history.record("amazon", "orders", 5)
        self.assertEqual(len(history.recent("amazon", "orders", shop_id="shop_cfg")), 1)
        self.assertEqual(history.recent("amazon", "orders", shop_id="default"), [])

    def test_legacy_db_migrates_into_default_shop(self):
        """
        老库(没有 shop_id 列、索引不含 shop_id)必须原地升级:
        老行全部归入 default 且不丢,索引重建为带 shop_id 的版本。
        """
        import sqlite3
        path = os.path.join(self.tmp, "legacy.db")
        with mock.patch.object(config, "HISTORY_DB", path):
            history.close()
            now = time.time()
            moment = datetime.datetime.fromtimestamp(now)
            conn = sqlite3.connect(path)
            conn.executescript("""
                CREATE TABLE metric_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL, key TEXT NOT NULL,
                    ts REAL NOT NULL, hour INTEGER NOT NULL,
                    dow INTEGER, value REAL NOT NULL);
                CREATE INDEX idx_mh_series ON metric_history(platform, key, ts);
            """)
            conn.execute("INSERT INTO metric_history (platform,key,ts,hour,dow,value) "
                         "VALUES ('amazon','orders',?,?,?,?)",
                         (now, moment.hour, moment.weekday(), 42.0))
            conn.commit()
            conn.close()

            rows = history.recent("amazon", "orders", shop_id="default")
            self.assertEqual([v for _, v in rows], [42.0], "老数据必须归入 default 且不丢")

            conn = sqlite3.connect(path)
            cols = [r[2] for r in conn.execute("PRAGMA index_info(idx_mh_series)")]
            conn.close()
            self.assertIn("shop_id", cols, "老索引必须重建为带 shop_id 的版本")
            history.close()


if __name__ == "__main__":
    unittest.main()
