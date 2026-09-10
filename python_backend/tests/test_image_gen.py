"""
生图 skill 的回归测试 —— 锁住"风格必须被固定住"和"没配 Key 绝不能偷偷发请求"。

运行方式(不需要装任何第三方依赖):
    python -m unittest discover -s python_backend/tests -v

为什么这些点必须钉死:
  - 风格模板是这个模块存在的理由。一旦模型能自由发挥提示词,
    同一个 SKU 两次出图风格就不一致,店铺里就是一盘散沙。
  - 生图要花钱。如果"没配 Key"时代码还发出请求,或者失败被当成成功,
    轻则浪费额度,重则模型拿着不存在的图片继续往下编描述。
"""
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="ecom_sop_test_")
os.environ.setdefault("STATE_FILE", os.path.join(_TMP, "state.json"))
os.environ.setdefault("HISTORY_DB", os.path.join(_TMP, "history.db"))

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# ---------------------------------------------------------------- 桩: requests
# 真实网络不该出现在回归测试里;每个用例按需 patch image_gen.requests.post。
_requests = types.ModuleType("requests")


def _no_post(*a, **k):  # pragma: no cover - 未被 patch 时被调用即视为测试失败
    raise AssertionError("本用例不应发起真实请求")


_requests.post = _no_post
_requests.exceptions = types.SimpleNamespace(RequestException=Exception)
sys.modules.setdefault("requests", _requests)

import config          # noqa: E402
import image_gen       # noqa: E402


class _Resp:
    """最小的响应桩:只要有 status_code / json / text 就够用。"""

    def __init__(self, payload, text=None, status=200):
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class TestStylePresets(unittest.TestCase):
    """风格模板:固定住图片风格的核心。"""

    def test_builtin_styles_exist(self):
        styles = image_gen.all_styles()
        for key in ("amazon_main", "scene", "detail", "lifestyle"):
            self.assertIn(key, styles)
            self.assertTrue(styles[key].get("prompt"))
            self.assertTrue(styles[key].get("aspect_ratio"))

    def test_style_determines_prompt_and_size(self):
        """同一个商品换风格,提示词和画幅必须跟着变 —— 否则风格没生效。"""
        a = image_gen.build_image_request({"subject": "earbuds", "style": "amazon_main"})
        b = image_gen.build_image_request({"subject": "earbuds", "style": "scene"})
        self.assertNotEqual(a["prompt"], b["prompt"])
        self.assertEqual(a["size"], "1024x1024")   # 1:1
        self.assertEqual(b["size"], "1152x896")    # 4:3
        self.assertIn("white background", a["prompt"])
        self.assertIn("Lifestyle", b["prompt"])

    def test_style_is_stable_across_calls(self):
        """同风格同输入必须完全一致 —— 风格是"固定"的,不是随机的。"""
        args = {"subject": "stainless bottle", "style": "detail", "count": 2}
        self.assertEqual(
            image_gen.build_image_request(args),
            image_gen.build_image_request(args),
        )

    def test_unknown_style_falls_back_to_main(self):
        req = image_gen.build_image_request({"subject": "cup", "style": "不存在的风格"})
        self.assertEqual(req["style"], "amazon_main")
        self.assertIn("white background", req["prompt"])

    def test_tool_definition_locks_style_to_enum(self):
        """模型只能从枚举里选风格,不能自创 —— 这是"固定风格"的落地点。"""
        props = image_gen.tool_definition()["function"]["parameters"]["properties"]
        self.assertIn("enum", props["style"])
        self.assertEqual(
            sorted(props["style"]["enum"]),
            sorted(image_gen.all_styles().keys()),
        )

    def test_custom_style_json_overrides_builtin(self):
        custom = json.dumps({
            "my_brand": {"label": "品牌风", "prompt": "Brand style shot of {subject}.",
                         "negative": "ugly", "aspect_ratio": "16:9"},
            "scene": {"label": "改过的场景图", "prompt": "Overridden {subject}."},
        })
        with mock.patch.object(config, "IMAGE_STYLES_JSON", custom):
            styles = image_gen.all_styles()
            self.assertIn("my_brand", styles)
            self.assertEqual(styles["scene"]["prompt"], "Overridden {subject}.")
            req = image_gen.build_image_request({"subject": "cup", "style": "my_brand"})
            self.assertEqual(req["size"], "1344x768")   # 16:9
            self.assertIn("Brand style shot", req["prompt"])

    def test_bad_custom_style_json_is_ignored_not_fatal(self):
        with mock.patch.object(config, "IMAGE_STYLES_JSON", "{不是合法JSON"):
            # 解析失败不能让进程挂掉,退回内置模板
            self.assertIn("amazon_main", image_gen.all_styles())


class TestRequestBuilding(unittest.TestCase):

    def test_empty_subject_rejected(self):
        with self.assertRaises(ValueError):
            image_gen.build_image_request({})

    def test_count_is_clamped(self):
        self.assertEqual(image_gen.build_image_request({"subject": "x", "count": 0})["count"], 1)
        self.assertEqual(image_gen.build_image_request({"subject": "x", "count": 99})["count"], 8)
        self.assertEqual(image_gen.build_image_request({"subject": "x", "count": 3})["count"], 3)

    def test_invalid_count_falls_back_to_default(self):
        with mock.patch.object(config, "IMAGE_DEFAULT_COUNT", 6):
            req = image_gen.build_image_request({"subject": "x", "count": "abc"})
            self.assertEqual(req["count"], 6)

    def test_selling_points_string_is_accepted(self):
        """模型有时把数组写成字符串,不能因此报错。"""
        req = image_gen.build_image_request({"subject": "cup", "selling_points": "316不锈钢"})
        self.assertEqual(req["selling_points"], ["316不锈钢"])
        self.assertIn("316不锈钢", req["prompt"])

    def test_prompt_always_includes_subject_even_if_template_omits_it(self):
        custom = json.dumps({"plain": {"label": "纯模板", "prompt": "A nice photo.", "aspect_ratio": "1:1"}})
        with mock.patch.object(config, "IMAGE_STYLES_JSON", custom):
            req = image_gen.build_image_request({"subject": "red cup", "style": "plain"})
            self.assertIn("red cup", req["prompt"])


class TestGenerate(unittest.TestCase):

    def test_unconfigured_never_sends_request(self):
        """没配 Key 时绝不能发请求 —— 生图是按张计费的。"""
        post = mock.MagicMock(side_effect=AssertionError("本用例不应发起真实请求"))
        with mock.patch.object(config, "IMAGE_ENABLED", False), \
             mock.patch.object(config, "IMAGE_API_KEY", ""), \
             mock.patch.object(config, "IMAGE_API_BASE_URL", ""), \
             mock.patch.object(image_gen.requests, "post", post):
            res = image_gen.generate(image_gen.build_image_request({"subject": "x"}))
        self.assertFalse(res["ok"])
        self.assertEqual(res["images"], [])
        self.assertIn("未配置", res["error"])
        post.assert_not_called()

    def test_request_body_carries_prompt_size_and_negative(self):
        captured = {}

        def fake_post(url, **kw):
            captured["url"] = url
            captured["json"] = kw.get("json")
            captured["headers"] = kw.get("headers")
            return _Resp({"data": [{"url": "https://cdn.test/a.png"}]})

        with mock.patch.multiple(config, **{
            "IMAGE_ENABLED": True, "IMAGE_API_KEY": "k",
            "IMAGE_API_BASE_URL": "https://img.example.com/v1",
            "IMAGE_API_PATH": "/images/generations",
            "IMAGE_API_MODEL": "seedance", "IMAGE_TIMEOUT": 5,
        }), mock.patch.object(image_gen.requests, "post", fake_post):
            req = image_gen.build_image_request(
                {"subject": "earbuds", "style": "amazon_main", "count": 3})
            res = image_gen.generate(req)

        self.assertTrue(res["ok"])
        self.assertEqual(res["images"], ["https://cdn.test/a.png"])
        self.assertEqual(captured["url"], "https://img.example.com/v1/images/generations")
        body = captured["json"]
        self.assertEqual(body["n"], 3)
        self.assertEqual(body["size"], "1024x1024")
        self.assertIn("earbuds", body["prompt"])
        self.assertIn("watermark", body["negative_prompt"])
        self.assertEqual(captured["headers"]["Authorization"], "Bearer k")

    def test_style_extra_merges_provider_specific_params(self):
        """供应商私有字段通过风格模板的 extra 注入,不必改代码。"""
        custom = json.dumps({
            "with_extra": {"label": "带私有参数", "prompt": "{subject}", "aspect_ratio": "1:1",
                           "extra": {"seed": 123, "style_preset": "photographic"}},
        })
        captured = {}

        def fake_post(url, **kw):
            captured.update(kw.get("json") or {})
            return _Resp({"data": [{"url": "u"}]})

        with mock.patch.object(config, "IMAGE_STYLES_JSON", custom), \
             mock.patch.multiple(config, **{
                 "IMAGE_ENABLED": True, "IMAGE_API_KEY": "k",
                 "IMAGE_API_BASE_URL": "https://img.example.com", "IMAGE_TIMEOUT": 5,
             }), mock.patch.object(image_gen.requests, "post", fake_post):
            image_gen.generate(image_gen.build_image_request({"subject": "x", "style": "with_extra"}))

        self.assertEqual(captured.get("seed"), 123)
        self.assertEqual(captured.get("style_preset"), "photographic")


class TestResponseParsing(unittest.TestCase):

    def _run(self, payload):
        def fake_post(url, **kw):
            return _Resp(payload)
        with mock.patch.multiple(config, **{
            "IMAGE_ENABLED": True, "IMAGE_API_KEY": "k",
            "IMAGE_API_BASE_URL": "https://img.example.com", "IMAGE_TIMEOUT": 5,
        }), mock.patch.object(image_gen.requests, "post", fake_post):
            return image_gen.generate(image_gen.build_image_request({"subject": "x"}))

    def test_openai_style_data_url(self):
        res = self._run({"data": [{"url": "https://a/1.png"}, {"url": "https://a/2.png"}]})
        self.assertEqual(res["images"], ["https://a/1.png", "https://a/2.png"])

    def test_b64_json_is_returned_as_data_uri(self):
        res = self._run({"data": [{"b64_json": "AAAA"}]})
        self.assertEqual(res["images"], ["data:image/png;base64,AAAA"])

    def test_images_list_variant(self):
        res = self._run({"images": ["https://b/1.png"]})
        self.assertEqual(res["images"], ["https://b/1.png"])

    def test_success_without_images_is_an_error(self):
        """返回 200 但没图,必须报错 —— 不能让后面以为图已经生成好了。"""
        res = self._run({"data": []})
        self.assertFalse(res["ok"])
        self.assertIn("没解析出图片", res["error"])

    def test_network_error_does_not_raise(self):
        def boom(url, **kw):
            raise Exception("connection reset")
        with mock.patch.multiple(config, **{
            "IMAGE_ENABLED": True, "IMAGE_API_KEY": "k",
            "IMAGE_API_BASE_URL": "https://img.example.com", "IMAGE_TIMEOUT": 5,
        }), mock.patch.object(image_gen.requests, "post", boom):
            res = image_gen.generate(image_gen.build_image_request({"subject": "x"}))
        self.assertFalse(res["ok"])
        self.assertIn("connection reset", res["error"])


class TestToolCallHandling(unittest.TestCase):

    def test_arguments_may_be_json_string(self):
        """function calling 里 arguments 常常是字符串,直接当 dict 用会炸。"""
        with mock.patch.object(image_gen, "generate",
                               return_value={"ok": True, "images": ["u"], "error": ""}) as gen:
            image_gen.run_tool_call(json.dumps({"subject": "cup"}))
        self.assertEqual(gen.call_args[0][0]["subject"], "cup")

    def test_invalid_arguments_json_reports_error(self):
        res = image_gen.run_tool_call("{坏掉的JSON")
        self.assertFalse(res["ok"])
        self.assertIn("不是合法 JSON", res["error"])

    def test_missing_subject_reports_error_not_traceback(self):
        res = image_gen.run_tool_call({"style": "scene"})
        self.assertFalse(res["ok"])
        self.assertIn("subject", res["error"])

    def test_tool_result_reports_failure_honestly(self):
        """失败必须如实告诉模型,否则模型会拿着不存在的图继续编描述。"""
        msg = image_gen.tool_result_message("call_1", {"ok": False, "images": [], "error": "未配置"})
        self.assertEqual(msg["role"], "tool")
        self.assertEqual(msg["tool_call_id"], "call_1")
        payload = json.loads(msg["content"])
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["error"], "未配置")

    def test_parse_tool_call_from_code_fence(self):
        text = '好的:\n```json\n{"tool": "generate_product_images", "arguments": {"subject": "cup"}}\n```'
        self.assertEqual(image_gen.parse_tool_call(text), {"subject": "cup"})

    def test_parse_tool_call_ignores_normal_reply(self):
        self.assertIsNone(image_gen.parse_tool_call("这张图不需要生成。"))
        self.assertIsNone(image_gen.parse_tool_call(""))


class TestAgentLoop(unittest.TestCase):
    """模型 → 工具 → 结果喂回 → 模型继续说,这个闭环必须通。"""

    def _enable_llm(self):
        return (mock.patch.object(config, "LLM_ENABLED", True),
                mock.patch.object(config, "LLM_API_KEY", "k"))

    def test_llm_not_configured_short_circuits(self):
        with mock.patch.object(config, "LLM_ENABLED", False), \
             mock.patch.object(config, "LLM_API_KEY", ""):
            res = image_gen.run_with_image_tool("给这个商品配图")
        self.assertFalse(res["ok"])
        self.assertEqual(res["images"], [])
        self.assertIn("未配置", res["error"])

    def test_tool_call_executed_and_result_fed_back(self):
        first = {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": image_gen.TOOL_NAME,
                         "arguments": json.dumps({"subject": "earbuds", "style": "scene"})}}]}
        final = {"role": "assistant", "content": "已生成场景图。"}
        seen = []

        def fake(messages, use_tools=True):
            seen.append(list(messages))
            return first if len(seen) == 1 else final

        p1, p2 = self._enable_llm()
        with p1, p2, \
             mock.patch.object(image_gen, "call_llm", fake), \
             mock.patch.object(image_gen, "generate",
                               return_value={"ok": True, "images": ["https://x/1.png"], "error": ""}):
            res = image_gen.run_with_image_tool("配图")

        self.assertTrue(res["ok"])
        self.assertTrue(res["used_tool"])
        self.assertEqual(res["images"], ["https://x/1.png"])
        self.assertEqual(res["content"], "已生成场景图。")
        self.assertEqual(len(seen), 2)
        # 第二轮必须把工具结果带回去,否则模型不知道图生成成功没有
        self.assertTrue(any(m.get("role") == "tool" for m in seen[1]))

    def test_no_tool_call_returns_content_directly(self):
        p1, p2 = self._enable_llm()
        with p1, p2, mock.patch.object(
                image_gen, "call_llm",
                return_value={"role": "assistant", "content": "这款不需要配图。"}):
            res = image_gen.run_with_image_tool("需要配图吗")
        self.assertTrue(res["ok"])
        self.assertFalse(res["used_tool"])
        self.assertEqual(res["images"], [])
        self.assertEqual(res["content"], "这款不需要配图。")

    def test_model_without_tool_support_falls_back(self):
        """带 tools 报错的模型要能退化成普通对话,而不是直接失败。"""
        calls = []

        def fake(messages, use_tools=True):
            calls.append(use_tools)
            if use_tools:
                return None          # 模拟模型不接受 tools 参数
            return {"role": "assistant", "content": "降级回答。"}

        p1, p2 = self._enable_llm()
        with p1, p2, mock.patch.object(image_gen, "call_llm", fake):
            res = image_gen.run_with_image_tool("配图")
        self.assertEqual(calls, [True, False])
        self.assertTrue(res["ok"])
        self.assertEqual(res["content"], "降级回答。")

    def test_call_llm_sends_tools_definition(self):
        captured = {}

        def fake_post(url, **kw):
            captured.update(kw.get("json") or {})
            return _Resp({"choices": [{"message": {"role": "assistant", "content": "hi"}}]})

        with mock.patch.multiple(config, **{
            "LLM_ENABLED": True, "LLM_API_KEY": "k",
            "LLM_BASE_URL": "https://llm.example.com/v1", "LLM_MODEL": "m",
        }), mock.patch.object(image_gen.requests, "post", fake_post):
            msg = image_gen.call_llm([{"role": "user", "content": "x"}])

        self.assertEqual(msg["content"], "hi")
        self.assertEqual(len(captured.get("tools") or []), 1)
        self.assertEqual(captured["tools"][0]["function"]["name"], image_gen.TOOL_NAME)

    def test_run_tool_call_failure_is_reported_not_swallowed(self):
        """生图失败要如实进 tool message,不能让模型以为图已经有了。"""
        first = {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_9", "type": "function",
            "function": {"name": image_gen.TOOL_NAME, "arguments": "{}"}}]}
        p1, p2 = self._enable_llm()
        with p1, p2, mock.patch.object(image_gen, "call_llm",
                                       side_effect=[first, {"role": "assistant", "content": "生图失败"}]):
            res = image_gen.run_with_image_tool("配图")   # 未配生图服务 -> 失败
        self.assertTrue(res["ok"])          # 对话本身成功
        self.assertEqual(res["images"], [])  # 但没有图
        self.assertTrue(res["used_tool"])


if __name__ == "__main__":
    unittest.main()
