"""图片库 HTTP API。

启动:
    python python_backend/image_api.py

接口(所有接口都接受 ?shop_id=xxx,缺省用 config.SHOP_ID):
    GET  /api/images?gallery=hot&style=scene&limit=100&offset=0&shop_id=shop_xxx
    GET  /api/images/stats?shop_id=shop_xxx
    GET  /api/images/feedback?style=scene&shop_id=shop_xxx
    POST /api/images/{id}/mark?shop_id=shop_xxx  {"gallery":"hot", "style_guidance":"柔和暖光; 右侧留白"}
    DELETE /api/images/{id}?shop_id=shop_xxx
    GET  /media/{file_name}
    GET  /health

多店铺隔离:每个接口都会把 shop_id 透传给 image_library,后者在 SQL 里
作为**第一道**过滤条件(不是可选条件),所以不存在"忘了带店铺就看全库"的情况。
前端图片库页面会自动带上当前店铺的 id。

这是轻量 stdlib 服务，不引入 Flask；前端若 API 不可用会显示明确离线态，
不会把假数据伪装成真实图库。部署平台会注入 PORT，服务必须监听 0.0.0.0:$PORT。
"""
import json
import logging
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import config
import image_library

log = logging.getLogger(__name__)


def _shop_of(query: dict):
    """从查询串取 shop_id;没给就返回 None,由 image_library 落到 config.SHOP_ID。"""
    return (query.get("shop_id") or [None])[0] or None


def _cors_headers(handler):
    origin = getattr(config, "IMAGE_API_CORS_ORIGINS", "*") or "*"
    handler.send_header("Access-Control-Allow-Origin", origin)
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")


def _json_body(handler):
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        if length > 32 * 1024:
            return None
        return json.loads(handler.rfile.read(length) or b"{}")
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _json_response(handler, status, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    _cors_headers(handler)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _error(handler, status, message):
    _json_response(handler, status, {"ok": False, "error": message})


class ImageAPIHandler(BaseHTTPRequestHandler):
    server_version = "EcommerceImageAPI/1.0"

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)

    def handle_one_request(self):
        """
        请求结束后释放本线程缓存的 SQLite 连接。

        为什么必须在这里关:ThreadingHTTPServer 是**每个请求起一个线程**,
        而 image_library 把连接缓存在 threading.local() 里 —— 线程一结束,
        那个连接就再也没人持有,只能等 GC 回收。表现是:
          - 每个请求都新建一个 SQLite 连接,连接复用完全失效;
          - 解释器退出时报一堆 ResourceWarning: unclosed database(CI 里能看到);
          - 请求量大时可能耗尽文件描述符。
        连接在请求内是复用的(一个请求里多次查询共用一条),请求结束就关掉。
        """
        try:
            super().handle_one_request()
        finally:
            image_library.close()

    def do_OPTIONS(self):
        self.send_response(204)
        _cors_headers(self)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        if path == "/health":
            _json_response(self, 200, {"ok": True, "service": "image-library-api",
                                       "shop_id": image_library._shop(None)})
            return
        if path == "/api/images":
            try:
                rows = image_library.list_assets(
                    gallery=(query.get("gallery") or [None])[0],
                    style=(query.get("style") or [None])[0],
                    limit=int((query.get("limit") or [100])[0]),
                    offset=int((query.get("offset") or [0])[0]),
                    shop_id=_shop_of(query),
                )
                _json_response(self, 200, {"ok": True, "items": rows,
                                           "count": len(rows)})
            except (ValueError, TypeError):
                _error(self, 400, "limit / offset 必须是数字")
            return
        if path == "/api/images/stats":
            _json_response(self, 200, {"ok": True, **image_library.stats(shop_id=_shop_of(query))})
            return
        if path == "/api/images/feedback":
            style = (query.get("style") or [""])[0]
            if not style:
                _error(self, 400, "style 不能为空")
                return
            feedback = image_library.style_feedback(style, shop_id=_shop_of(query))
            _json_response(self, 200, {"ok": True, "feedback": feedback})
            return
        if path.startswith("/media/"):
            self._serve_media(unquote(path[len("/media/"):]))
            return
        _error(self, 404, "接口不存在")

    def _serve_media(self, name):
        # 只允许图片库目录内的 basename，杜绝 .. 路径穿越。
        if not name or os.path.basename(name) != name or name.endswith(".json"):
            _error(self, 400, "非法文件名")
            return
        root = os.path.realpath(getattr(config, "IMAGE_STORE_DIR", ""))
        path = os.path.realpath(os.path.join(root, name))
        if not root or not path.startswith(root + os.sep) or not os.path.isfile(path):
            _error(self, 404, "图片不存在")
            return
        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            _error(self, 404, "图片读取失败")
            return
        self.send_response(200)
        _cors_headers(self)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        parsed = urlparse(self.path)
        parts = [unquote(x) for x in parsed.path.strip("/").split("/")]
        query = parse_qs(parsed.query)
        if len(parts) != 4 or parts[:2] != ["api", "images"] or parts[3] != "mark":
            _error(self, 404, "接口不存在")
            return
        try:
            asset_id = int(parts[2])
        except ValueError:
            _error(self, 400, "图片 id 不合法")
            return
        body = _json_body(self)
        if not isinstance(body, dict) or body.get("gallery") not in image_library.GALLERIES:
            _error(self, 400, "gallery 必须是 hot / normal / unclassified")
            return
        try:
            # shop_id 一路带到 UPDATE 的 WHERE 里:换个店铺的 id 是改不动别人的图的。
            row = image_library.mark(asset_id, body["gallery"], body.get("style_guidance"),
                                     shop_id=_shop_of(query))
        except ValueError as e:
            _error(self, 400, str(e))
            return
        if not row:
            _error(self, 404, "图片不存在或图库不可用")
            return
        _json_response(self, 200, {"ok": True, "item": row})

    def do_DELETE(self):
        parsed = urlparse(self.path)
        parts = [unquote(x) for x in parsed.path.strip("/").split("/")]
        query = parse_qs(parsed.query)
        if len(parts) != 3 or parts[:2] != ["api", "images"]:
            _error(self, 404, "接口不存在")
            return
        try:
            asset_id = int(parts[2])
        except ValueError:
            _error(self, 400, "图片 id 不合法")
            return
        if not image_library.delete(asset_id, shop_id=_shop_of(query)):
            _error(self, 404, "图片不存在或图库不可用")
            return
        _json_response(self, 200, {"ok": True, "deleted": asset_id})


def serve(host=None, port=None):
    # 云平台通过 PORT 注入监听端口；本地未注入时仍用 8765。
    host = host or os.getenv("PORT") and "0.0.0.0" or getattr(config, "IMAGE_API_HOST", "127.0.0.1")
    port = int(port or os.getenv("PORT") or getattr(config, "IMAGE_API_PORT", 8765))
    server = ThreadingHTTPServer((host, port), ImageAPIHandler)
    log.info("图片库 API 已启动: http://%s:%s", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        image_library.close()
        server.server_close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    serve(host=os.getenv("IMAGE_API_HOST"), port=os.getenv("IMAGE_API_PORT"))
