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


class TestShopIsolation(unittest.TestCase):
    """多店铺(防关联多账号):图库必须按 shop_id 隔离。"""

    @classmethod
    def setUpClass(cls):
        image_library.close()

    def setUp(self):
        image_library.close()
        self.db = tempfile.mktemp(prefix="library_shop_", suffix=".db")
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

    def _record(self, text):
        data = text.encode()
        return {
            "ok": True, "path": "/tmp/" + text + ".png", "name": text + ".png",
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "meta": {"style": "scene", "style_label": "场景氛围图", "subject": text,
                     "selling_points": [], "prompt": "prompt " + text,
                     "aspect_ratio": "4:3", "size": "2304x1728", "style_guidance": ""},
        }

    def test_same_image_can_be_registered_in_two_shops(self):
        """
        旧结构的唯一键是**全局** sha256,于是 B 店注册一张 A 店已存过的图时,
        ON CONFLICT 会命中 A 店那一行 —— B 店的图库列表里什么都不出现,也不报错。
        多店铺下这是静默丢数据,所以唯一键必须改成 (shop_id, sha256)。
        这条用例在旧结构上必然失败(第二次 add_asset 返回的是 A 店那行)。
        """
        rec = self._record("dup")
        a = image_library.add_asset(rec, shop_id="shop_a")
        b = image_library.add_asset(rec, shop_id="shop_b")
        self.assertIsNotNone(a)
        self.assertIsNotNone(b, "同一张图必须能在另一家店单独登记")
        self.assertNotEqual(a["id"], b["id"], "两家店应该是两条独立资产")
        self.assertEqual(a["shop_id"], "shop_a")
        self.assertEqual(b["shop_id"], "shop_b")
        self.assertEqual(len(image_library.list_assets(shop_id="shop_a")), 1)
        self.assertEqual(len(image_library.list_assets(shop_id="shop_b")), 1)

    def test_list_and_stats_are_scoped(self):
        image_library.add_asset(self._record("x"), "hot", shop_id="shop_a")
        image_library.add_asset(self._record("y"), "normal", shop_id="shop_b")
        self.assertEqual(image_library.stats(shop_id="shop_a")["hot"], 1)
        self.assertEqual(image_library.stats(shop_id="shop_a")["total"], 1)
        self.assertEqual(image_library.stats(shop_id="shop_b")["normal"], 1)
        self.assertEqual(image_library.stats(shop_id="shop_c")["total"], 0)
        self.assertEqual(len(image_library.list_assets("hot", shop_id="shop_a")), 1)
        self.assertEqual(len(image_library.list_assets("hot", shop_id="shop_b")), 0)

    def test_mark_and_delete_do_not_cross_shops(self):
        """用 B 店的身份去改 A 店的图必须无效 —— id 是全局自增的,撞到别人 id 完全可能。"""
        a = image_library.add_asset(self._record("a"), shop_id="shop_a")
        self.assertIsNone(image_library.mark(a["id"], "hot", "偷改", shop_id="shop_b"))
        self.assertEqual(image_library.get(a["id"], shop_id="shop_a")["gallery"], "unclassified")
        self.assertFalse(image_library.delete(a["id"], shop_id="shop_b"))
        self.assertIsNotNone(image_library.get(a["id"], shop_id="shop_a"))

    def test_style_feedback_is_scoped(self):
        """A 店攒够的爆款风格不能反哺到 B 店 —— 否则等于用别家的调性污染本店出图。"""
        for i in range(3):
            r = image_library.add_asset(self._record("a%d" % i), "hot", shop_id="shop_a")
            image_library.mark(r["id"], "hot", "暖光; 留白", shop_id="shop_a")
        self.assertIsNotNone(image_library.style_feedback("scene", shop_id="shop_a"))
        self.assertIsNone(image_library.style_feedback("scene", shop_id="shop_b"),
                          "A 店的爆款风格不该出现在 B 店")

    def test_default_shop_follows_config(self):
        with mock.patch.object(config, "SHOP_ID", "shop_cfg"):
            image_library.add_asset(self._record("z"))
            self.assertEqual(image_library.stats()["total"], 1)
        self.assertEqual(image_library.stats(shop_id="default")["total"], 0)

    def test_legacy_global_unique_table_is_migrated(self):
        """
        老库(sha256 全局唯一)必须能原地升级成 (shop_id, sha256) 唯一,且一行不丢。
        这是重建表的迁移路径,单独钉一条,防止哪天被"简化"掉。
        """
        import sqlite3
        image_library.close()
        conn = sqlite3.connect(self.db)
        conn.executescript("""
            CREATE TABLE image_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL, file_name TEXT NOT NULL,
                sha256 TEXT NOT NULL UNIQUE,
                gallery TEXT NOT NULL DEFAULT 'unclassified',
                style TEXT NOT NULL DEFAULT '', style_label TEXT NOT NULL DEFAULT '',
                subject TEXT NOT NULL DEFAULT '', selling_points TEXT NOT NULL DEFAULT '[]',
                prompt TEXT NOT NULL DEFAULT '', aspect_ratio TEXT NOT NULL DEFAULT '',
                size TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
                bytes INTEGER NOT NULL DEFAULT 0, style_guidance TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL, updated_at REAL NOT NULL);
        """)
        conn.execute(
            "INSERT INTO image_assets (file_path,file_name,sha256,gallery,style,created_at,updated_at) "
            "VALUES ('/tmp/old.png','old.png','oldsha','hot','scene',1.0,1.0)")
        conn.commit()
        conn.close()

        # 老库没有 shop_id 列,升级后老行必须归入 default 且不丢
        rows = image_library.list_assets(shop_id="default")
        self.assertEqual(len(rows), 1, "老数据必须归入 default 店铺")
        self.assertEqual(rows[0]["gallery"], "hot")

        # 升级后同一 sha256 可以在另一家店登记(旧结构会静默失败)
        rec = self._record("oldsha")
        rec["sha256"] = "oldsha"
        self.assertIsNotNone(image_library.add_asset(rec, shop_id="shop_b"))

        # 索引也必须带 shop_id
        conn = sqlite3.connect(self.db)
        cols = [r[2] for r in conn.execute("PRAGMA index_info(idx_image_gallery)")]
        conn.close()
        self.assertIn("shop_id", cols)


if __name__ == "__main__":
    unittest.main()
