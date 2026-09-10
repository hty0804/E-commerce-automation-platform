"""图片库 HTTP API 测试，不启动真实端口。"""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from unittest import mock

_TMP = tempfile.mkdtemp(prefix="ecom_sop_api_test_")
os.environ["IMAGE_LIBRARY_DB"] = os.path.join(_TMP, "library.db")
os.environ["IMAGE_STORE_DIR"] = os.path.join(_TMP, "images")
_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# requests 仅是其它模块 import 依赖,本测试不发出 requests 网络调用
if "requests" not in sys.modules:
    requests_stub = type(sys)("requests")
    requests_stub.get = lambda *a, **k: None
    requests_stub.post = lambda *a, **k: None
    sys.modules["requests"] = requests_stub

import config       # noqa: E402
import image_api    # noqa: E402
import image_library # noqa: E402


class TestImageAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        image_library.close()
        cls.server = image_api.ThreadingHTTPServer(("127.0.0.1", 0), image_api.ImageAPIHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        image_library.close()

    def setUp(self):
        image_library.close()
        image_library._connect(write=True)
        # 清空前一测试的资产
        conn = image_library._connect(write=True)
        conn.execute("DELETE FROM image_assets")
        conn.commit()

    def req(self, method, path, body=None):
        c = HTTPConnection("127.0.0.1", self.port, timeout=3)
        raw = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        c.request(method, path, raw, {"Content-Type": "application/json"} if raw else {})
        r = c.getresponse()
        data = r.read()
        return r.status, json.loads(data) if data else None, dict(r.getheaders())

    def asset(self):
        return image_library.add_asset({
            "ok": True, "path": "/tmp/a.png", "name": "a.png", "sha256": "api-sha",
            "bytes": 10, "meta": {"style": "scene", "style_label": "场景氛围图", "subject": "cup"}
        })

    def test_stats_and_list(self):
        self.asset()
        status, data, headers = self.req("GET", "/api/images/stats")
        self.assertEqual(status, 200)
        self.assertEqual(data["total"], 1)
        self.assertIn("Access-Control-Allow-Origin", headers)
        status, data, _ = self.req("GET", "/api/images?gallery=unclassified&style=scene")
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 1)

    def test_mark_hot_and_feedback(self):
        # 门槛设为 1 只为 API 测试快；门槛逻辑由 image_library 测试覆盖
        with mock.patch.object(config, "IMAGE_HOT_MIN_SAMPLES", 1):
            row = self.asset()
            status, data, _ = self.req("POST", f"/api/images/{row['id']}/mark",
                                      {"gallery": "hot", "style_guidance": "柔和暖光"})
            self.assertEqual(status, 200)
            self.assertEqual(data["item"]["gallery"], "hot")
            status, data, _ = self.req("GET", "/api/images/feedback?style=scene")
            self.assertEqual(status, 200)
            self.assertEqual(data["feedback"]["guidance"], "柔和暖光")

    def test_bad_mark_and_missing_asset(self):
        status, data, _ = self.req("POST", "/api/images/999/mark", {"gallery": "favorite"})
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])
        status, data, _ = self.req("GET", "/api/images/feedback")
        self.assertEqual(status, 400)

    def test_delete_only_index(self):
        row = self.asset()
        status, data, _ = self.req("DELETE", f"/api/images/{row['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(data["deleted"], row["id"])
        self.assertIsNone(image_library.get(row["id"]))

    def test_media_path_traversal_blocked(self):
        status, data, _ = self.req("GET", "/media/../library.db")
        self.assertIn(status, (400, 404))
        self.assertFalse(data["ok"])


if __name__ == "__main__":
    unittest.main()
