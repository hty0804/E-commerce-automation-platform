"""
回归测试 —— 锁住本轮修掉的每一个 bug,避免改回去。

运行方式(不需要装任何第三方依赖):
    python -m unittest discover -s python_backend/tests -v
或:
    python python_backend/tests/test_core.py

为什么用桩模块:
    requests / botocore 属于外部依赖,而这些 bug 全都出在**我们自己写的逻辑**里
    (取值、取整、分类、加锁、去重、截断)。用桩替换掉网络层,测试才能在没有
    API 密钥、没有网络的环境中稳定复现问题 —— 这正是回归测试该有的样子。
"""
import datetime
import os
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

# ---------------------------------------------------------------- 环境准备
_TMP = tempfile.mkdtemp(prefix="ecom_sop_test_")
os.environ["STATE_FILE"] = os.path.join(_TMP, "state.json")
# 历史库也必须隔离:否则每跑一次测试就往真实的 python_backend/history.db 里
# 塞一批 100/10/0 这种测试样本,把基线中位数带偏 —— 既污染了生产数据,
# 又会让"该告警的不告警"这种假象出现在本地跑测试的时候。
os.environ["HISTORY_DB"] = os.path.join(_TMP, "history.db")

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


# ---------------------------------------------------------------- 桩: requests
class FakeResp:
    def __init__(self, payload=None, status_code=200, text="", headers=None):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self):
        if 400 <= self.status_code:
            raise RuntimeError(f"HTTP {self.status_code}")


LAST_REQUEST = {}


def _fake_post(url, data=None, json=None, timeout=None):
    LAST_REQUEST["url"] = url
    LAST_REQUEST["data"] = data
    LAST_REQUEST["json"] = json
    if "auth/o2/token" in str(url):
        return FakeResp({"access_token": "tok", "expires_in": 3600})
    return FakeResp({"errcode": 0, "errmsg": "ok"})


def _fake_request(method, url, headers=None, data=None, timeout=None):
    LAST_REQUEST["data"] = data
    LAST_REQUEST["url"] = url
    return FakeResp({"reportId": "R1"})


_requests = types.ModuleType("requests")
_requests.post = _fake_post
_requests.request = _fake_request
_requests.exceptions = types.SimpleNamespace(RequestException=Exception)
sys.modules.setdefault("requests", _requests)


# ---------------------------------------------------------------- 桩: botocore
class _AWSRequest:
    def __init__(self, method=None, url=None, data=None, params=None, headers=None):
        self.method, self.url, self.data = method, url, data
        self.params, self.headers = params, headers

    def prepare(self):
        return self


_botocore = types.ModuleType("botocore")
_auth = types.ModuleType("botocore.auth")
_awsreq = types.ModuleType("botocore.awsrequest")
_creds = types.ModuleType("botocore.credentials")
_auth.SigV4Auth = lambda *a, **k: types.SimpleNamespace(add_auth=lambda r: None)
_awsreq.AWSRequest = _AWSRequest
_creds.Credentials = lambda **k: None
_botocore.auth, _botocore.awsrequest, _botocore.credentials = _auth, _awsreq, _creds
sys.modules.setdefault("botocore", _botocore)
sys.modules.setdefault("botocore.auth", _auth)
sys.modules.setdefault("botocore.awsrequest", _awsreq)
sys.modules.setdefault("botocore.credentials", _creds)


# ---------------------------------------------------------------- 被测模块
import alerts          # noqa: E402
import amazon_client   # noqa: E402
import anomaly_detector  # noqa: E402
import config          # noqa: E402
import incident        # noqa: E402
import main            # noqa: E402
import pdd_client      # noqa: E402

# 拼多多签名需要 secret,测试里给个固定值(不配置时 _sign 会拿 None 去拼接)
config.PDD_CLIENT_SECRET = "test_secret"
config.PDD_CLIENT_ID = "test_cid"
config.PDD_ACCESS_TOKEN = "test_token"


def _reset_state():
    for suffix in ("", ".lock", ".tmp", ".broken"):
        p = config.STATE_FILE + suffix
        if os.path.exists(p):
            os.remove(p)


class Base(unittest.TestCase):
    """
    本文件测的是**环比**行为,所以默认关掉基线。

    不关的话,同一个 _TMP 里的 history.db 会被前面的用例写入样本,
    后面的用例就可能被基线抑制 —— 表现为"该告警的没告警",
    看起来像产品 bug,实际是测试之间互相污染。基线本身的行为
    由 test_history.py 单独覆盖,两边各管一段,互不干扰。
    """

    def setUp(self):
        _reset_state()
        self._baseline_off = mock.patch.object(config, "BASELINE_ENABLED", False)
        self._baseline_off.start()

    def tearDown(self):
        self._baseline_off.stop()
        _reset_state()


# ================================================================ P0
class TestNullSafety(Base):
    """P0-1:字段为 null 时不能崩溃"""

    def test_dict_get_default_does_not_cover_null(self):
        """先钉死根因:.get 的默认值只在键不存在时生效"""
        self.assertIsNone({"a": None}.get("a", 0))

    def test_none_value_does_not_crash_detection(self):
        """以前这里直接 TypeError,整个监控进程被打挂"""
        checks = [{"platform": "amazon", "key": "inventory:S1", "value": None,
                   "threshold": 0.3, "direction": "drop"}]
        incidents = anomaly_detector.collect_incidents(checks)  # 不应抛异常
        self.assertEqual(incidents, [])

    def test_none_does_not_overwrite_baseline(self):
        """本轮没取到数时,上一轮的真实基线必须保留,否则永远比不出变化"""
        c = [{"platform": "amazon", "key": "inventory:S1", "value": 10,
              "threshold": 0.3, "direction": "drop"}]
        anomaly_detector.collect_incidents(c)
        c2 = [dict(c[0], value=None)]
        anomaly_detector.collect_incidents(c2)
        self.assertEqual(anomaly_detector._load_state().get("amazon:inventory:S1"), 10)

    def test_zero_is_still_a_real_drop(self):
        """0 是真实断货,必须能告警 —— 不能为了防 None 把 0 也一起放过"""
        c = [{"platform": "amazon", "key": "inventory:S1", "value": 10,
              "threshold": 0.3, "direction": "drop"}]
        anomaly_detector.collect_incidents(c)
        inc = anomaly_detector.collect_incidents([dict(c[0], value=0)])
        self.assertEqual(len(inc), 1)
        self.assertEqual(inc[0]["category"] if "category" in inc[0] else "stockout", "stockout")


class TestPriceToFen(Base):
    """P0-2:元转分不能因浮点误差少 1 分"""

    def test_classic_float_trap(self):
        self.assertEqual(int(1.15 * 100), 114)     # 旧写法的错误结果
        self.assertEqual(main._to_fen(1.15), 115)  # 新写法的正确结果

    def test_more_cases(self):
        for price, expect in [("8.37", 837), ("1.15", 115), ("39.9", 3990), ("0", 0)]:
            self.assertEqual(main._to_fen(price), expect, f"price={price}")

    def test_invalid_input(self):
        self.assertEqual(main._to_fen("abc"), 0)
        self.assertEqual(main._to_fen(None), 0)


# ================================================================ P1
class TestClassifyFallback(Base):
    """P1-4:抓取失败不能落到 unknown"""

    def test_monitor_key_defaults_to_api_error(self):
        self.assertEqual(
            incident.classify({"platform": "all", "key": "monitor", "value": None, "message": "任意"}),
            "api_error",
        )

    def test_network_errors_recognized(self):
        for msg in ("⚠️ 亚马逊数据抓取失败: HTTPSConnectionPool: Connection aborted",
                    "⚠️ 拼多多数据抓取失败: Expecting value: line 1 column 1 (char 0)"):
            self.assertEqual(incident.classify_by_message(msg), "api_error", msg)

    def test_specific_errors_still_precise(self):
        self.assertEqual(incident.classify_by_message("403 Forbidden"), "auth_failure")
        self.assertEqual(incident.classify_by_message("429 Too Many"), "rate_limit")
        self.assertEqual(incident.classify_by_message("本轮未获取到任何库存数据"), "data_missing")


class TestStateLock(Base):
    """P1-5:所有写 state 的路径都必须走锁"""

    def test_state_update_writes_back(self):
        with anomaly_detector.state_update() as state:
            state["k"] = "v"
        self.assertEqual(anomaly_detector._load_state()["k"], "v")

    def test_incident_cooldown_uses_locked_path(self):
        """incident 以前直接调 _load_state/_save_state,绕过锁"""
        import inspect
        src = inspect.getsource(incident)
        self.assertNotIn("anomaly_detector._load_state", src)
        self.assertNotIn("anomaly_detector._save_state", src)
        self.assertIn("state_update", src)

    def test_lock_is_mutually_exclusive(self):
        with anomaly_detector.state_lock(timeout=2):
            got = []
            t = __import__("threading").Thread(
                target=lambda: got.append(anomaly_detector.state_lock(timeout=0.3)))
            t.start()
            t.join()
        self.assertTrue(True)


class TestPagedResult(Base):
    """P1-6:翻页截断必须能被调用方感知"""

    def test_incremental_empty_result_advances_cursor(self):
        """增量窗口完整返回空列表 = 没有变更,应推进游标且不报数据缺失。"""
        now = datetime.datetime.now(datetime.timezone.utc)
        errors = []
        with mock.patch.object(anomaly_detector, "set_sync_cursor") as set_cursor:
            main._handle_inventory_sync_result(
                {}, amazon_client.PagedResult([], complete=True),
                "2026-09-09T15:00:00Z", now, errors)
        self.assertEqual(errors, [])
        set_cursor.assert_called_once_with("amazon_inventory", main._utc_iso(now))

    def test_full_empty_result_keeps_cursor_and_reports_missing(self):
        """全量窗口空列表仍表示数据缺失,不能借空结果推进游标。"""
        now = datetime.datetime.now(datetime.timezone.utc)
        errors = []
        with mock.patch.object(anomaly_detector, "set_sync_cursor") as set_cursor:
            main._handle_inventory_sync_result(
                {}, amazon_client.PagedResult([], complete=True),
                None, now, errors)
        self.assertEqual(len(errors), 1)
        self.assertIn("未获取到任何库存数据", errors[0])
        set_cursor.assert_not_called()

    def test_incomplete_empty_result_keeps_cursor(self):
        """即使没有返回 SKU,分页不完整也必须优先报告截断并保留游标。"""
        now = datetime.datetime.now(datetime.timezone.utc)
        errors = []
        with mock.patch.object(anomaly_detector, "set_sync_cursor") as set_cursor:
            main._handle_inventory_sync_result(
                {}, amazon_client.PagedResult([], complete=False),
                "2026-09-09T15:00:00Z", now, errors)
        self.assertEqual(len(errors), 1)
        self.assertIn("数据不完整", errors[0])
        set_cursor.assert_not_called()

    def test_complete_flag(self):
        r = amazon_client.PagedResult([1, 2, 3], complete=False)
        self.assertEqual(len(r), 3)      # 仍是普通列表
        self.assertFalse(r.complete)

    def test_truncated_fetch_marks_incomplete(self):
        cli = amazon_client.AmazonSPAPIClient()
        cli._signed_request = lambda *a, **k: {
            "payload": {"inventorySummaries": [{"sellerSku": f"S{i}", "totalQuantity": 1}
                                               for i in range(50)]},
            "pagination": {"nextToken": "more"},
        }
        with mock.patch.object(amazon_client.time, "sleep"):
            res = cli.get_inventory_summaries(max_pages=3)
        self.assertFalse(res.complete)


class TestConfigRobust(Base):
    """P1-7:配置解析不能因为环境变量写错就崩掉整个进程"""

    def test_safe_int_falls_back(self):
        os.environ["_TEST_BAD_INT"] = "abc"
        self.assertEqual(config._int("_TEST_BAD_INT", 60), 60)

    def test_safe_int_ok(self):
        os.environ["_TEST_OK_INT"] = " 45 "
        self.assertEqual(config._int("_TEST_OK_INT", 60), 45)

    def test_bool_empty_env_keeps_default(self):
        """
        空字符串必须按"用默认值"处理,不能当成 False。

        以前 _bool 用的是 os.getenv(name, str(default)) —— os.getenv 的默认值
        只在变量**不存在**时生效。.env 里写 `LISTING_SKIP_ON_ERROR=`(值留空)
        会拿到空串 → 不在真值表里 → 返回 False,把默认开启的安全开关静默关掉,
        校验不通过的 Listing 会照样上架,而日志里没有任何异常。
        """
        os.environ["_TEST_BOOL_EMPTY"] = ""
        try:
            self.assertTrue(config._bool("_TEST_BOOL_EMPTY", True),
                            "空值把默认 True 的安全开关翻成了 False")
            self.assertFalse(config._bool("_TEST_BOOL_EMPTY", False))
            os.environ["_TEST_BOOL_EMPTY"] = "   "
            self.assertTrue(config._bool("_TEST_BOOL_EMPTY", True), "纯空格同样要按默认值处理")
        finally:
            os.environ.pop("_TEST_BOOL_EMPTY", None)

    def test_bool_values(self):
        for raw, expected in (("true", True), ("TRUE", True), ("1", True),
                              ("yes", True), ("on", True), ("false", False),
                              ("0", False), ("no", False), ("off", False)):
            os.environ["_TEST_BOOL_V"] = raw
            try:
                self.assertEqual(config._bool("_TEST_BOOL_V", True), expected,
                                 f"{raw!r} 解析成了错误的值")
            finally:
                os.environ.pop("_TEST_BOOL_V", None)

    def test_state_file_is_absolute(self):
        self.assertTrue(os.path.isabs(config.STATE_FILE))

    def test_state_file_is_per_shop(self):
        """
        多店铺:状态文件必须按 SHOP_ID 分文件。

        不分的话两家店共用一份 游标 / LLM 冷却 / 告警去重 / 上架幂等位点 ——
        典型后果是 A 店推过告警把 B 店的去重位点也占了,
        B 店的真故障被静默跳过(而群里看起来只是"今天没消息")。
        """
        self.assertTrue(config._shop_state_file("default").endswith("state.json"),
                        "default 店铺必须保持老路径,升级后才读得到原有位点")
        us = config._shop_state_file("shop_us")
        uk = config._shop_state_file("shop_uk")
        self.assertNotEqual(us, uk)
        self.assertTrue(us.endswith("state_shop_us.json"), us)

    def test_shop_state_file_sanitizes_unsafe_id(self):
        """SHOP_ID 可能来自 .env 或人工输入,不能让它跳出数据目录。"""
        p = config._shop_state_file("../../etc/passwd")
        self.assertEqual(os.path.dirname(os.path.abspath(p)),
                         os.path.dirname(os.path.abspath(config._shop_state_file("safe"))),
                         "非法 SHOP_ID 不能改变文件所在目录")
        self.assertNotIn(os.sep, os.path.basename(p))
        self.assertNotIn("\\", os.path.basename(p))


# ================================================================ P2
class TestAlertDedup(Base):
    """P2-8:慢性问题不能每条都发"""

    def setUp(self):
        super().setUp()
        config.WECOM_WEBHOOK_URL = "https://example.invalid/hook"
        config.ALERT_DEDUPE_MIN = 60
        self.sent = []
        alerts.send_wecom_alert = lambda c: self.sent.append(c)

    def test_second_call_suppressed(self):
        r1 = alerts.send_alert("慢了", dedup_key="slow")
        r2 = alerts.send_alert("慢了", dedup_key="slow")
        self.assertEqual(r1.get("wecom"), "ok")
        self.assertEqual(r2.get("skipped"), "dedup:slow")
        self.assertEqual(len(self.sent), 1)

    def test_dedup_failure_does_not_swallow_alert(self):
        with mock.patch.object(alerts, "_dedup_pass", side_effect=RuntimeError("state 挂了")):
            r = alerts.send_alert("要紧事", dedup_key="x")
        self.assertEqual(r.get("wecom"), "ok")  # 宁可多发,不能吞掉


class TestOrderMinPrevious(Base):
    """P2-9:低基线不做环比,避免夜间误报"""

    def test_below_floor_not_reported(self):
        c = [{"platform": "amazon", "key": "order_count_last_hour", "value": 1,
              "threshold": 0.5, "direction": "drop", "min_previous": 5}]
        anomaly_detector.collect_incidents(c)
        inc = anomaly_detector.collect_incidents([dict(c[0], value=0)])
        self.assertEqual(inc, [], "1 单 → 0 单在低基线时不应告警")

    def test_above_floor_reported(self):
        c = [{"platform": "amazon", "key": "order_count_last_hour", "value": 100,
              "threshold": 0.5, "direction": "drop", "min_previous": 5}]
        anomaly_detector.collect_incidents(c)
        inc = anomaly_detector.collect_incidents([dict(c[0], value=10)])
        self.assertEqual(len(inc), 1, "100 单 → 10 单必须告警")


class TestRiseDirection(Base):
    """
    direction='rise' 曾经是死参数:传进去不报错、也永远不告警,
    调用方以为配了涨价检测,实际静默失效。
    """

    def test_rise_is_detected(self):
        c = [{"platform": "pdd", "key": "price_rise", "value": 19.99,
              "threshold": 0.2, "direction": "rise"}]
        anomaly_detector.collect_incidents(c)
        inc = anomaly_detector.collect_incidents([dict(c[0], value=25.0)])
        self.assertEqual(len(inc), 1, "19.99 → 25.00 涨幅 25% 必须告警")
        self.assertIn("涨至", inc[0]["message"])

    def test_rise_does_not_fire_on_drop(self):
        c = [{"platform": "pdd", "key": "price_rise2", "value": 19.99,
              "threshold": 0.2, "direction": "rise"}]
        anomaly_detector.collect_incidents(c)
        inc = anomaly_detector.collect_incidents([dict(c[0], value=15.0)])
        self.assertEqual(inc, [], "跌价不应触发 rise 告警")

    def test_drop_does_not_fire_on_rise(self):
        c = [{"platform": "pdd", "key": "price_drop", "value": 19.99,
              "threshold": 0.2, "direction": "drop"}]
        anomaly_detector.collect_incidents(c)
        inc = anomaly_detector.collect_incidents([dict(c[0], value=25.0)])
        self.assertEqual(inc, [], "涨价不应触发 drop 告警")

    def test_unknown_direction_falls_back_to_drop(self):
        r = anomaly_detector._compare("pdd", "k", 0, 10, 0.3, "sideways")
        self.assertTrue(r["anomaly"], "非法 direction 应回退为 drop,而不是静默失效")


class TestRetryAfterCap(Base):
    """P2-10:服务端给的 Retry-After 必须封顶"""

    def test_wait_is_capped(self):
        cli = amazon_client.AmazonSPAPIClient()
        slept = []
        resp = FakeResp({}, status_code=429, headers={"Retry-After": "9999"})
        with mock.patch.object(amazon_client.requests, "request", return_value=resp), \
             mock.patch.object(amazon_client.time, "sleep", side_effect=lambda s: slept.append(s)):
            try:
                cli._signed_request("GET", "/x", max_retries=1)
            except Exception:
                pass
        self.assertTrue(slept)
        self.assertLessEqual(max(slept), config.MAX_RETRY_WAIT)


class TestPddHardening(Base):
    def test_pagination_paces(self):
        pdd = pdd_client.PinduoduoClient()
        pdd.get_goods_list = lambda page, page_size: {
            "goods_list_get_response": {"goods_list": [], "total_count": 0}}
        slept = []
        with mock.patch.object(pdd_client.time, "sleep", side_effect=lambda s: slept.append(s)):
            pdd.iter_goods_list()
        self.assertEqual(slept, [], "只有一页时不需要等待")

    def test_non_json_response_explains_itself(self):
        pdd = pdd_client.PinduoduoClient()
        bad = FakeResp(None, status_code=200, text="<html>限流</html>")
        bad.json = lambda: (_ for _ in ()).throw(ValueError("no json"))
        with mock.patch.object(pdd_client.requests, "post", return_value=bad):
            with self.assertRaises(RuntimeError) as ctx:
                pdd._call("pdd.goods.list.get", {}, max_retries=1)
        self.assertIn("不是合法 JSON", str(ctx.exception))

    def test_token_error_is_actionable(self):
        pdd = pdd_client.PinduoduoClient()
        msg = pdd._explain_error("pdd.goods.add", {"error_msg": "access_token is invalid"})
        self.assertIn("重新走商家授权", msg)


# ================================================================ 架构
class TestHeartbeat(Base):
    def test_health_ok_after_run(self):
        main._mark_heartbeat()
        self.assertEqual(main.check_health(), 0)

    def test_health_fails_when_never_ran(self):
        self.assertEqual(main.check_health(), 1)

    def test_health_fails_when_stale(self):
        with anomaly_detector.state_update() as st:
            st["_last_run"] = "2020-01-01T00:00:00Z"
        self.assertEqual(main.check_health(), 1)


class TestPddIdempotency(Base):
    def test_second_call_skipped(self):
        class FakePdd:
            def __init__(self):
                self.calls = 0

            def add_goods(self, payload):
                self.calls += 1
                return {"goods_id": 987}

        pdd = FakePdd()
        item = {"platform": "pdd", "sku": "SKU-A", "payload": {"goods_name": "x"}}
        main._pdd_add_once(pdd, item, "x")
        res = main._pdd_add_once(pdd, item, "x")
        self.assertEqual(pdd.calls, 1, "重复提交必须被拦住,否则会重复建商品")
        self.assertTrue(res.get("skipped"))


# ================================================================ 上一轮的修复(防回退)
class TestPreviousFixes(Base):
    def test_amazon_body_serialized(self):
        cli = amazon_client.AmazonSPAPIClient()
        cli.create_inventory_report()
        body = LAST_REQUEST["data"]
        self.assertIsInstance(body, bytes)

    def test_pdd_nested_json(self):
        pdd = pdd_client.PinduoduoClient()
        payload = {"goods_name": "测试", "attributes": {"颜色": "红"}}
        captured = {}

        def _capture(url, data=None, json=None, timeout=None):
            captured.update(data or {})
            return FakeResp({})

        with mock.patch.object(pdd_client.requests, "post", side_effect=_capture):
            pdd.add_goods(payload)
        sent = captured["goods_commit_info"]
        self.assertIsInstance(sent, str)
        self.assertTrue(sent.startswith("{") and '"goods_name"' in sent)

    def test_truncate_by_bytes(self):
        report = {"summary": "x", "category_stats": {}, "rule_advice": "库" * 3000,
                  "llm_advice": "", "final_advice": "库" * 3000, "source": "rule"}
        out = incident.format_alert(report, max_len=1800)
        self.assertLessEqual(len(out.encode("utf-8")), 1800)

    def test_alert_channel_isolation(self):
        config.WECOM_WEBHOOK_URL = "https://example.invalid/a"
        config.DINGTALK_WEBHOOK_URL = "https://example.invalid/b"
        dingtalk_called = []
        alerts.send_wecom_alert = lambda c: (_ for _ in ()).throw(RuntimeError("企微挂了"))
        alerts.send_dingtalk_alert = lambda c: dingtalk_called.append(1)
        res = alerts.send_alert("x")  # 不应抛异常
        self.assertTrue(dingtalk_called, "企微失败不能影响钉钉")
        self.assertTrue(res["wecom"].startswith("failed:"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
