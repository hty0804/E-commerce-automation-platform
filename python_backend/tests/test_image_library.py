"""图片库与爆款反哺测试。"""
import hashlib
import os
import sys
import tempfile
import unittest
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="ecom_sop_library_test_")
os.environ["IMAGE_LIBRARY_DB"] = os.path.join(_TMP, "library.db")
if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 单独跑该文件时也不要求机器全局装 requests;生图测试只用到请求组装。
if "requests" not in sys.modules:
    _requests = type(sys)("requests")
    _requests.post = lambda *a, **k: None
    _requests.get = lambda *a, **k: None
    sys.modules["requests"] = _requests

import config        # noqa: E402
import image_gen     # noqa: E402
import image_library # noqa: E402


def test_placeholder():
    # 保留文件可被 discover 导入
    return True


class TestImageLibrary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        image_library.close()

    def setUp(self):
        image_library.close()
        # 每个用例用独立 DB,避免状态互相污染
        self.db = tempfile.mktemp(prefix="library_case_", suffix=".db")
        self.patch = mock.patch.object(config, "IMAGE_LIBRARY_DB", self.db)
        self.patch.start()

    def tearDown(self):
        image_library.close()
        self.patch.stop()
        for p in (self.db, self.db + "-wal", self.db + "-shm"):
            try:
                os.remove(p)
            except FileNotFoundError:
                pass

    def _record(self, text, style="scene", guidance=""):
        data = text.encode()
        return {
            "ok": True, "path": "/tmp/" + text + ".png", "name": text + ".png",
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "meta": {"style": style, "style_label": "场景氛围图", "subject": text,
                     "selling_points": ["卖点"], "prompt": "prompt " + text,
                     "aspect_ratio": "4:3", "size": "2304x1728",
                     "style_guidance": guidance},
        }

    def test_add_is_idempotent_by_sha(self):
        a = image_library.add_asset(self._record("a"))
        b = image_library.add_asset(self._record("a"), "hot")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(image_library.stats()["total"], 1)
        # 重复注册只更新路径,不会偷偷把人工 gallery 改回 unclassified
        self.assertEqual(image_library.get(a["id"])["gallery"], "unclassified")

    def test_gallery_validation(self):
        with self.assertRaises(ValueError):
            image_library.add_asset(self._record("a"), "favorite")

    def test_list_and_mark_gallery(self):
        a = image_library.add_asset(self._record("a"))
        self.assertEqual(len(image_library.list_assets("unclassified")), 1)
        changed = image_library.mark(a["id"], "hot", "柔和暖光; 木质背景; 右侧留白")
        self.assertEqual(changed["gallery"], "hot")
        self.assertEqual(changed["style_guidance"], "柔和暖光; 木质背景; 右侧留白")
        self.assertEqual(len(image_library.list_assets("hot", "scene")), 1)

    def test_feedback_requires_three_hot_samples(self):
        for i in range(2):
            a = image_library.add_asset(self._record(str(i)))
            image_library.mark(a["id"], "hot", "柔和暖光")
        self.assertIsNone(image_library.style_feedback("scene"))
        a = image_library.add_asset(self._record("2"))
        image_library.mark(a["id"], "hot", "柔和暖光")
        feedback = image_library.style_feedback("scene")
        self.assertEqual(feedback["sample_count"], 3)
        self.assertEqual(feedback["guidance"], "柔和暖光")

    def test_normal_images_do_not_feedback(self):
        for i in range(5):
            a = image_library.add_asset(self._record(str(i)))
            image_library.mark(a["id"], "normal", "某个普通备注")
        self.assertIsNone(image_library.style_feedback("scene"))

    def test_feedback_uses_unique_guidance_and_caps_length(self):
        for i in range(3):
            a = image_library.add_asset(self._record(str(i)))
            image_library.mark(a["id"], "hot", "暖光; 留白")
        f = image_library.style_feedback("scene", max_chars=5)
        self.assertEqual(f["guidance"], "暖光; 留")

    def test_delete_only_removes_index(self):
        a = image_library.add_asset(self._record("a"))
        self.assertTrue(image_library.delete(a["id"]))
        self.assertIsNone(image_library.get(a["id"]))
        self.assertFalse(image_library.delete(a["id"]))

    def test_add_saved_batch(self):
        result = image_library.add_saved_batch({
            "saved": [self._record("a"), self._record("b")],
            "meta": {"style": "detail", "style_label": "细节特写"},
        }, "normal")
        self.assertEqual(result["count"], 2)
        self.assertEqual(image_library.stats()["normal"], 2)


class TestStyleInjection(unittest.TestCase):
    def test_build_request_reads_feedback(self):
        original = image_gen.image_library if hasattr(image_gen, "image_library") else None
        class FakeLibrary:
            @staticmethod
            def style_feedback(style):
                return {"guidance": "柔和暖光; 右侧留白"}
        # image_gen 是延迟 import,用 sys.modules 注入 fake
        old = sys.modules.get("image_library")
        sys.modules["image_library"] = FakeLibrary
        try:
            req = image_gen.build_image_request({"subject": "cup", "style": "scene"})
            self.assertIn("柔和暖光", req["prompt"])
            self.assertEqual(req["style_guidance"], "柔和暖光; 右侧留白")
        finally:
            if old is None:
                sys.modules.pop("image_library", None)
            else:
                sys.modules["image_library"] = old


if __name__ == "__main__":
    unittest.main()
