"""
test_rules_sync.py —— 锁住「前后端规则必须同源」这件事。

背景:
  Listing 生成与校验的规则(长度红线 / 违规词 / 类目 schema / 中英词表 / emoji 区间)
  以前在 python_backend/listing_gen.py 和 assets/js/store.js 各硬编码一份,
  并且**已经实际漂移过**:前端漏了 best-seller / cure / 100% cure 三个违规词,
  于是出现「前端显示校验通过、后端却拦截」,而 cure 属于医疗功效类合规高危词。

  现在规则统一放在 shared/listing_rules.json,前端的 assets/js/listing_rules.js
  由 tools/gen_listing_rules.py 生成。这个文件就是防止有人改了 JSON 忘记重新生成、
  或者又在某一端偷偷抄一份。

运行:
    python -m unittest discover -s python_backend/tests -v
"""
import io
import json
import os
import re
import sys
import types
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_BACKEND)

RULES_JSON = os.path.join(_ROOT, "shared", "listing_rules.json")
RULES_JS = os.path.join(_ROOT, "assets", "js", "listing_rules.js")
INDEX_HTML = os.path.join(_ROOT, "index.html")

# listing_gen 依赖 requests，这里用桩顶掉（本测试只关心规则数据）
if "requests" not in sys.modules:
    sys.modules["requests"] = types.ModuleType("requests")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import listing_gen  # noqa: E402


def _read(path):
    with io.open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_js_rules():
    """把 window.LISTING_RULES = {...}; 里的 JSON 抠出来解析"""
    src = _read(RULES_JS)
    i = src.find("window.LISTING_RULES = ")
    assert i >= 0, "listing_rules.js 里找不到 window.LISTING_RULES"
    body = src[i + len("window.LISTING_RULES = "):].rstrip()
    body = body.rstrip(";").strip()
    return json.loads(body)


class TestRulesSource(unittest.TestCase):
    """共享数据源本身必须存在且完整"""

    @classmethod
    def setUpClass(cls):
        cls.rules = json.loads(_read(RULES_JSON))

    def test_json_exists(self):
        self.assertTrue(os.path.exists(RULES_JSON), "shared/listing_rules.json 不见了")

    def test_required_keys(self):
        for k in ("limits", "banned_words", "category_schema", "default_schema",
                  "cn2en", "emoji_ranges", "repeat_punct", "top_level_fields"):
            self.assertIn(k, self.rules, f"缺少规则键 {k}")

    def test_regression_banned_words_present(self):
        """这三个词曾经被前端漏掉，不准再消失"""
        words = [w for w, _ in self.rules["banned_words"]]
        for w in ("best-seller", "cure", "100% cure"):
            self.assertIn(w, words, f"违规词 {w} 被移除了（前端曾因漏掉它误放行）")

    def test_every_category_has_required_and_recommended(self):
        for name, sch in self.rules["category_schema"].items():
            if name.startswith("_"):
                continue
            self.assertTrue(sch["required"], f"{name} 没有 required")
            self.assertTrue(sch["recommended"], f"{name} 没有 recommended")


class TestGeneratedJsInSync(unittest.TestCase):
    """生成的 JS 必须与 JSON 完全一致 —— 防『改了 JSON 忘了重跑生成器』"""

    def test_js_matches_json(self):
        rules = json.loads(_read(RULES_JSON))
        expected = json.dumps(rules, ensure_ascii=False, indent=2, sort_keys=False)
        actual = json.dumps(_load_js_rules(), ensure_ascii=False, indent=2, sort_keys=False)
        self.assertEqual(
            actual, expected,
            "assets/js/listing_rules.js 与 shared/listing_rules.json 不一致。\n"
            "改了 JSON 之后请重跑: python tools/gen_listing_rules.py",
        )

    def test_js_is_marked_generated(self):
        self.assertIn("自动生成", _read(RULES_JS), "生成文件里应标明是自动生成的")


class TestPythonUsesSharedSource(unittest.TestCase):
    """Python 端不得再持有自己的副本"""

    def test_banned_words_from_json(self):
        rules = json.loads(_read(RULES_JSON))
        self.assertEqual(
            [list(x) for x in listing_gen.BANNED_WORDS],
            [list(x) for x in rules["banned_words"]],
        )

    def test_limits_from_json(self):
        rules = json.loads(_read(RULES_JSON))
        self.assertEqual(listing_gen.LIMITS, rules["limits"])

    def test_no_hardcoded_copy_left(self):
        """listing_gen.py 里不应再有 best seller 之类的硬编码违规词表"""
        src = _read(os.path.join(_BACKEND, "listing_gen.py"))
        self.assertNotIn('("best seller", "平台禁止使用销量排名类表述")', src)
        self.assertNotIn('("无线蓝牙耳机", "Wireless Bluetooth Earbuds")', src)

    def test_emoji_regex_built_from_ranges(self):
        rules = json.loads(_read(RULES_JSON))
        pat = listing_gen._emoji_pattern()
        for lo, hi in rules["emoji_ranges"]:
            # 区间端点必须能被正则命中，否则说明区间没生效
            self.assertIsNotNone(listing_gen._EMOJI_RE.search(chr(lo)),
                                 f"码点 {lo:#x} 未被 emoji 正则覆盖")
            self.assertIsNotNone(listing_gen._EMOJI_RE.search(chr(hi)),
                                 f"码点 {hi:#x} 未被 emoji 正则覆盖")
        self.assertIn("{2,}", pat, "连续感叹号判定丢了")


class TestFrontendConsistency(unittest.TestCase):
    """前端拿到的必须和后端是同一份"""

    def test_banned_words_identical(self):
        js_words = [w for w, _ in _load_js_rules()["banned_words"]]
        py_words = [w for w, _ in listing_gen.BANNED_WORDS]
        self.assertEqual(js_words, py_words, "前后端违规词表不一致")

    def test_emoji_ranges_identical(self):
        js = _load_js_rules()
        self.assertEqual(js["emoji_ranges"],
                         json.loads(_read(RULES_JSON))["emoji_ranges"])

    def test_index_html_loads_rules_before_store(self):
        html = _read(INDEX_HTML)
        srcs = re.findall(r'<script src="([^"]+)"></script>', html)
        self.assertIn("assets/js/listing_rules.js", srcs,
                      "index.html 没有引入 listing_rules.js")
        self.assertIn("assets/js/store.js", srcs)
        self.assertLess(srcs.index("assets/js/listing_rules.js"),
                        srcs.index("assets/js/store.js"),
                        "listing_rules.js 必须在 store.js 之前加载")

    def test_store_js_has_no_hardcoded_rules(self):
        """
        注意要查「硬编码的数据」而不是变量名:
        LISTING_LIMITS / BANNED_WORDS 这些变量名还在(视图层在用),
        但现在是从共享规则推导出来的,内容不再是写死的字面量。
        """
        src = _read(os.path.join(_ROOT, "assets", "js", "store.js"))
        for literal in ("titleMax: 200", "kwBytes: 250", "'best seller', '平台禁止",
                        "var BANNED_WORDS = [", "var CN2EN = [", "var SCENE_BY_CATEGORY = {"):
            self.assertNotIn(literal, src, f"store.js 里又出现了硬编码规则: {literal}")
        self.assertIn("global.LISTING_RULES", src, "store.js 没有从共享规则加载")
        self.assertIn("var LISTING_LIMITS = {}", src,
                      "LISTING_LIMITS 应由共享规则推导，而不是写死字面量")


if __name__ == "__main__":
    unittest.main(verbosity=2)
