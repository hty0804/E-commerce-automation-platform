"""
生图转存的回归测试。

为什么这些点必须钉死:
  方舟的图片 URL 只有 24 小时有效期 —— 不转存就是满屏死链,而且是**延迟爆炸**
  的那种(当时一切正常,过一天才坏)。转存是图库的地基,地基错了后面全错。

运行方式(不需要装任何第三方依赖):
    python -m unittest discover -s python_backend/tests -v
"""
import base64
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="ecom_sop_store_test_")
os.environ.setdefault("STATE_FILE", os.path.join(_TMP, "state.json"))
os.environ.setdefault("HISTORY_DB", os.path.join(_TMP, "history.db"))
os.environ["IMAGE_STORE_DIR"] = os.path.join(_TMP, "images")

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

_requests = types.ModuleType("requests")


def _no_call(*a, **k):  # pragma: no cover
    raise AssertionError("本用例不应发起真实请求")


_requests.post = _no_call
_requests.get = _no_call
_requests.exceptions = types.SimpleNamespace(RequestException=Exception)
sys.modules.setdefault("requests", _requests)

# 别的测试文件可能已经注册过 requests 桩(有些只桩了 post)。
# 这里统一补齐 get —— 否则 mock.patch.object(..., "get") 会报
# "does not have the attribute 'get'",而且是**单独跑没问题、全量跑才炸**,
# 这种依赖导入顺序的失败最难查。
_SHARED = sys.modules["requests"]
if not hasattr(_SHARED, "get"):
    _SHARED.get = _no_call

import config       # noqa: E402
import image_store  # noqa: E402


class _Resp:
    """最小的下载响应桩:支持 iter_content + status_code + headers。"""

    def __init__(self, data: bytes, status: int = 200, content_type: str = "image/jpeg"):
        self._data = data
        self.status_code = status
        self.headers = {"Content-Type": content_type}

    def iter_content(self, size=8192):
        for i in range(0, len(self._data), size):
            yield self._data[i:i + size]


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


class TestSaveImage(unittest.TestCase):

    def _patch_get(self, payload: bytes, **kw):
        return mock.patch.object(image_store.requests, "get",
                                 lambda url, **k: _Resp(payload, **kw))

    def test_url_is_downloaded_and_persisted(self):
        with self._patch_get(JPEG):
            rec = image_store.save_image("https://ark.cn/a.jpg", {"style": "amazon_main"})
        self.assertTrue(rec["ok"], rec.get("error"))
        self.assertTrue(os.path.exists(rec["path"]))
        self.assertEqual(rec["bytes"], len(JPEG))
        self.assertTrue(rec["name"].endswith(".jpg"))

    def test_data_uri_is_decoded(self):
        src = "data:image/png;base64," + base64.b64encode(PNG).decode()
        rec = image_store.save_image(src, {"style": "scene"})
        self.assertTrue(rec["ok"], rec.get("error"))
        with open(rec["path"], "rb") as f:
            self.assertEqual(f.read(), PNG)
        self.assertTrue(rec["name"].endswith(".png"))

    def test_extension_comes_from_content_type_not_url(self):
        """URL 结尾写 .jpg 但实际是 png —— 信 URL 就会存错扩展名。"""
        with self._patch_get(PNG, content_type="image/png"):
            rec = image_store.save_image("https://ark.cn/whatever.jpg")
        self.assertTrue(rec["name"].endswith(".png"), rec["name"])

    def test_extension_falls_back_to_magic_bytes(self):
        """Content-Type 给 application/octet-stream 时靠文件头判断。"""
        with self._patch_get(PNG, content_type="application/octet-stream"):
            rec = image_store.save_image("https://ark.cn/x")
        self.assertTrue(rec["name"].endswith(".png"), rec["name"])

    def test_content_type_is_used_when_magic_bytes_are_unknown(self):
        """
        BMP 的魔数不在 _MAGIC 表里,只有响应头能定扩展名。

        这条用例是为了钉死一个真实 bug:旧实现的 _fetch() 只 return bytes,
        把响应头丢了,于是 save_image 里"看 Content-Type"这条路径从来没生效,
        一律兜底成 .jpg —— 扩展名和内容不符,前端按 jpeg 解码直接裂图。
        旧代码在这里会得到 .jpg,所以本用例在修复前必然失败。
        """
        bmp = b"BM" + b"\x00" * 60          # 魔数 BM 不在 _MAGIC 里
        with self._patch_get(bmp, content_type="image/bmp"):
            rec = image_store.save_image("https://ark.cn/whatever.jpg")
        self.assertTrue(rec["ok"], rec.get("error"))
        self.assertTrue(rec["name"].endswith(".bmp"), rec["name"])
        self.assertEqual(rec["content_type"], "image/bmp")

    def test_data_uri_declared_type_is_honoured(self):
        """data:image/bmp;base64,... 的类型写在头部,也不该被丢掉。"""
        bmp = b"BM" + b"\x00" * 60
        src = "data:image/bmp;base64," + base64.b64encode(bmp).decode()
        rec = image_store.save_image(src)
        self.assertTrue(rec["ok"], rec.get("error"))
        self.assertTrue(rec["name"].endswith(".bmp"), rec["name"])

    def test_sidecar_records_where_it_came_from(self):
        """没有 sidecar,磁盘上就是一堆没来历的孤儿文件,没法做人工筛选。"""
        url = "https://ark.cn/origin.png"
        with self._patch_get(PNG):
            rec = image_store.save_image(url, {"style": "detail", "subject": "red cup"})
        side = rec["path"] + ".json"
        self.assertTrue(os.path.exists(side))
        with open(side, encoding="utf-8") as f:
            meta = json.load(f)
        self.assertEqual(meta["style"], "detail")
        self.assertEqual(meta["subject"], "red cup")
        self.assertEqual(meta["source"], url)
        self.assertEqual(meta["sha256"], rec["sha256"])
        self.assertTrue(meta["saved_at"])

    def test_same_content_is_not_stored_twice(self):
        """内容哈希同名 => 重跑一次不会把图库撑爆。"""
        with self._patch_get(JPEG):
            a = image_store.save_image("https://ark.cn/1.jpg")
            b = image_store.save_image("https://ark.cn/2.jpg")   # 不同 URL,同样内容
        self.assertEqual(a["name"], b["name"])
        self.assertEqual(a["sha256"], b["sha256"])

    def test_http_error_is_reported_not_raised(self):
        with self._patch_get(b"", status=403):
            rec = image_store.save_image("https://ark.cn/gone.jpg")
        self.assertFalse(rec["ok"])
        self.assertIn("403", rec["error"])

    def test_oversized_download_is_aborted(self):
        """超限时必须一个字节都不落盘 —— 半截文件比没有更糟。"""
        sub = tempfile.mkdtemp(prefix="ecom_sop_oversize_")
        big = b"x" * (1024 * 1024)
        with mock.patch.object(config, "IMAGE_STORE_MAX_BYTES", 4096), \
             mock.patch.object(config, "IMAGE_STORE_DIR", sub), \
             self._patch_get(big):
            rec = image_store.save_image("https://ark.cn/huge.jpg")
        self.assertFalse(rec["ok"])
        self.assertIn("上限", rec["error"])
        self.assertEqual(rec["path"], "")
        self.assertEqual(os.listdir(sub), [])

    def test_unknown_source_is_reported_not_raised(self):
        rec = image_store.save_image("file:///etc/passwd")
        self.assertFalse(rec["ok"])
        self.assertIn("不认识", rec["error"])

    def test_network_exception_is_captured(self):
        with mock.patch.object(image_store.requests, "get",
                               side_effect=Exception("连接超时")):
            rec = image_store.save_image("https://ark.cn/x.jpg")
        self.assertFalse(rec["ok"])
        self.assertIn("连接超时", rec["error"])

    def test_style_is_sanitized_into_filename(self):
        with self._patch_get(JPEG):
            rec = image_store.save_image("https://ark.cn/a.jpg", {"style": "爆款风/复杂 名"})
        self.assertNotIn("/", os.path.basename(rec["path"]))
        self.assertIn("爆款风", rec["name"])


class TestSaveImages(unittest.TestCase):

    def test_batch_splits_saved_and_failed(self):
        def fake_get(url, **k):
            if "bad" in url:
                return _Resp(b"", status=500)
            return _Resp(JPEG)

        with mock.patch.object(image_store.requests, "get", fake_get):
            res = image_store.save_images(
                ["https://ark.cn/ok1.jpg", "https://ark.cn/bad.jpg", "https://ark.cn/ok2.jpg"],
                {"style": "amazon_main"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["count"], 2)
        self.assertEqual(len(res["failed"]), 1)
        self.assertIn("500", res["failed"][0]["error"])
        self.assertEqual(res["dir"], image_store.store_dir())

    def test_all_failed_means_not_ok(self):
        with mock.patch.object(image_store.requests, "get",
                               lambda url, **k: _Resp(b"", status=404)):
            res = image_store.save_images(["https://ark.cn/a.jpg"])
        self.assertFalse(res["ok"])
        self.assertEqual(res["count"], 0)

    def test_empty_input_is_safe(self):
        res = image_store.save_images([])
        self.assertFalse(res["ok"])
        self.assertEqual(res["count"], 0)
        self.assertEqual(res["saved"], [])


class TestStoreGenerated(unittest.TestCase):

    def test_style_and_subject_carry_into_sidecar(self):
        """人工筛选时最需要"什么风格 + 给哪个商品"这两条,必须自动带上。"""
        result = {
            "ok": True,
            "images": ["https://ark.cn/a.jpg", "https://ark.cn/b.jpg"],
            "request": {
                "style": "scene", "style_label": "场景氛围图",
                "subject": "black earbuds", "selling_points": ["35dB降噪"],
                "prompt": "Lifestyle scene photo of black earbuds",
                "aspect_ratio": "4:3", "size": "2304x1728",
            },
        }
        with mock.patch.object(image_store.requests, "get", lambda url, **k: _Resp(JPEG)):
            res = image_store.store_generated(result)
        self.assertEqual(res["count"], 2)
        with open(res["saved"][0]["path"] + ".json", encoding="utf-8") as f:
            meta = json.load(f)
        self.assertEqual(meta["style"], "scene")
        self.assertEqual(meta["subject"], "black earbuds")
        self.assertEqual(meta["selling_points"], ["35dB降噪"])
        self.assertEqual(meta["aspect_ratio"], "4:3")

    def test_failed_generation_stores_nothing(self):
        res = image_store.store_generated({"ok": False, "images": [], "error": "接口报错"})
        self.assertFalse(res["ok"])
        self.assertEqual(res["count"], 0)


class TestListStored(unittest.TestCase):

    def _isolated_dir(self):
        """每个用例用独立目录 —— 否则会被前面用例写进去的文件污染。"""
        sub = tempfile.mkdtemp(prefix="ecom_sop_list_")
        return mock.patch.object(config, "IMAGE_STORE_DIR", sub)

    def test_scan_returns_all_sidecars(self):
        # 两张内容必须不同:内容相同会被去重成一份(dedupe 是有意的)
        def fake_get(url, **k):
            return _Resp(PNG) if "2" in url else _Resp(JPEG)

        with self._isolated_dir(), \
             mock.patch.object(image_store.requests, "get", fake_get):
            image_store.save_images(["https://ark.cn/1.jpg", "https://ark.cn/2.jpg"],
                                    {"style": "amazon_main"})
            items = image_store.list_stored()
        self.assertEqual(len(items), 2)
        self.assertTrue(all(i.get("file") for i in items))

    def test_broken_sidecar_is_skipped_not_fatal(self):
        """一个 sidecar 坏了不该让整个图库打不开。"""
        with self._isolated_dir() as _:
            d = image_store.store_dir()
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "broken.json"), "w", encoding="utf-8") as f:
                f.write("{这不是JSON")
            with mock.patch.object(image_store.requests, "get", lambda url, **k: _Resp(JPEG)):
                image_store.save_image("https://ark.cn/1.jpg", {"style": "amazon_main"})
            items = image_store.list_stored()
        self.assertEqual(len(items), 1)


if __name__ == "__main__":
    unittest.main()
