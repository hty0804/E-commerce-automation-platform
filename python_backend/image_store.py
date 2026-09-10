"""
image_store.py —— 生图结果落盘转存(图库的底座)

为什么必须有这一层:
  火山方舟返回的图片 URL **只有 24 小时有效期**。如果把 URL 直接存进图库,
  第二天打开就是满屏死链 —— 而且这种失败是"延迟爆炸"的:生图当时一切正常,
  过一天才坏,排查时你根本不会想到是 URL 过期。所以生完必须**立刻下载落盘**,
  图库里存本地路径。

设计取舍:
  - 文件名带内容哈希(sha256 前 12 位):同一张图重复生成不会存两份,
    顺便天然幂等 —— 重跑一次不会把图库撑爆。
  - 每张图旁边写一个同名 .json 的 sidecar,记下它从哪来、用的什么风格、
    提示词是什么。否则磁盘上就是一堆没有来历的孤儿文件,做人工筛选时
    你连"这张是什么风格"都答不上来。图库直接扫 *.json 就能建索引。
  - 任何失败都不抛异常,只写进返回的 failed 列表 —— 转存失败不该影响主流程。
  - 扩展名**不信 URL**,看 Content-Type,看不出来再嗅探文件头魔数。
    URL 结尾写 .jpg 实际是 png 的情况很常见,存错扩展名后面处理会莫名其妙。

依赖:requests(已在 requirements.txt)。
"""
import hashlib
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

import requests

import config

log = logging.getLogger(__name__)

# 文件头魔数 -> 扩展名。Content-Type 缺失或给的是 application/octet-stream 时兜底。
_MAGIC = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"RIFF", ".webp"),   # RIFF....WEBP
)

_CONTENT_TYPE_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def store_dir() -> str:
    return str(getattr(config, "IMAGE_STORE_DIR", "") or "")


def _ensure_dir() -> str:
    d = store_dir()
    if not d:
        raise ValueError("IMAGE_STORE_DIR 为空,不知道往哪存")
    os.makedirs(d, exist_ok=True)
    return d


def _safe(text: str, maxlen: int = 24) -> str:
    """把风格名/商品名压成能进文件名的片段(保留中文)。"""
    s = re.sub(r"[^\w\-]+", "_", str(text or ""), flags=re.UNICODE).strip("_")
    return s[:maxlen]


def _ext(content_type: str, head: bytes) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in _CONTENT_TYPE_EXT:
        return _CONTENT_TYPE_EXT[ct]
    for magic, ext in _MAGIC:
        if head.startswith(magic):
            return ext
    return ".jpg"   # 兜底:方舟默认输出 jpeg


def _fetch(src: str) -> bytes:
    """
    取回图片字节。支持两种来源:
      - http(s) URL —— 方舟返回的就是这种
      - data URI   —— 接口配 response_format=b64_json 时我们拼出来的
    """
    if src.startswith("data:"):
        # data:image/png;base64,xxxx
        _, _, payload = src.partition(",")
        import base64
        return base64.b64decode(payload)

    if not src.startswith(("http://", "https://")):
        raise ValueError(f"不认识的来源: {src[:60]}")

    resp = requests.get(src, timeout=getattr(config, "IMAGE_STORE_TIMEOUT", 60),
                        stream=True)
    if resp.status_code >= 400:
        raise RuntimeError(f"下载失败 HTTP {resp.status_code}")

    max_bytes = int(getattr(config, "IMAGE_STORE_MAX_BYTES", 20 * 1024 * 1024))
    chunks: List[bytes] = []
    total = 0
    # 边下边算大小:不设上限的话,一个异常大的响应能把磁盘写满。
    for chunk in resp.iter_content(8192):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise RuntimeError(f"图片超过上限 {max_bytes} 字节,已中止下载")
        chunks.append(chunk)
    return b"".join(chunks)


def _write(path: str, data: bytes) -> None:
    """先写临时文件再 rename —— 避免进程被杀时留下半个损坏文件被当成正常图。"""
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def save_image(src: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    转存单张。返回 {"ok", "path", "name", "bytes", "sha256", "content_type", "error"}。
    **不抛异常** —— 失败写进 error,由调用方决定怎么处理。
    """
    try:
        data = _fetch(src)
        if not data:
            raise RuntimeError("下载到空内容")
    except Exception as e:
        log.warning("图片转存失败: %s", e)
        return {"ok": False, "path": "", "name": "", "bytes": 0,
                "sha256": "", "content_type": "", "error": str(e)}

    digest = hashlib.sha256(data).hexdigest()
    meta = meta or {}
    prefix = _safe(str(meta.get("style") or "")) or "img"
    ext = _ext(meta.get("content_type") or "", data[:12])
    name = f"{prefix}-{time.strftime('%Y%m%d')}-{digest[:12]}{ext}"

    try:
        d = _ensure_dir()
        path = os.path.join(d, name)
        if not os.path.exists(path):   # 内容哈希同名 => 已存过就不用重写
            _write(path, data)

        # data URI 会把整段 base64 塞进 sidecar,既没用又撑大文件,只记个摘要
        source = src[:500] if not src.startswith("data:") \
            else f"data:image/...;base64 ({len(src)} 字符,已转存为本地文件)"
        sidecar = dict(meta)
        sidecar.update({
            "file": name,
            "source": source,
            "sha256": digest,
            "bytes": len(data),
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        with open(os.path.join(d, name + ".json"), "w", encoding="utf-8") as f:
            json.dump(sidecar, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning("图片落盘失败: %s", e)
        return {"ok": False, "path": "", "name": name, "bytes": len(data),
                "sha256": digest, "content_type": "", "error": str(e)}

    return {
        "ok": True,
        "path": path,
        "name": name,
        "bytes": len(data),
        "sha256": digest,
        "content_type": "",
        "meta": dict(meta),
        "error": "",
    }


def save_images(srcs: List[str], meta: Optional[Dict[str, Any]] = None
                ) -> Dict[str, Any]:
    """
    批量转存。返回 {"ok", "saved": [...], "failed": [...], "dir", "count"}。

    ok = 至少成功一张。全失败时 ok=False 且 failed 里有每一条的原因。
    """
    saved: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for i, src in enumerate(srcs or []):
        rec = save_image(src, meta)
        rec["index"] = i
        (saved if rec["ok"] else failed).append(rec)

    return {
        "ok": bool(saved),
        "saved": saved,
        "failed": failed,
        "dir": store_dir(),
        "count": len(saved),
        "meta": dict(meta or {}),
    }


def store_generated(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    把 image_gen.generate() 的结果直接转存,顺手把风格/主体/提示词写进 sidecar。

    之所以要自动带这些:人工筛选时"这张是什么风格、给哪个商品出的"是最关键的
    两个信息,靠人后来补一定补不全。
    """
    req = result.get("request") or {}
    meta = {
        "style": req.get("style", ""),
        "style_label": req.get("style_label", ""),
        "subject": req.get("subject", ""),
        "selling_points": req.get("selling_points") or [],
        "prompt": req.get("prompt", ""),
        "aspect_ratio": req.get("aspect_ratio", ""),
        "size": req.get("size", ""),
        "style_guidance": req.get("style_guidance", ""),
    }
    return save_images(result.get("images") or [], meta)


def list_stored() -> List[Dict[str, Any]]:
    """
    扫描转存目录,返回所有图片的 sidecar 元数据(供图库建索引用)。

    只认 .json 且能解析的,坏文件跳过不报错 —— 一个 sidecar 坏了
    不该让整个图库打不开。
    """
    try:
        d = _ensure_dir()
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, fn), "r", encoding="utf-8") as f:
                rec = json.load(f)
            if isinstance(rec, dict):
                out.append(rec)
        except Exception as e:
            log.warning("跳过损坏的 sidecar %s: %s", fn, e)
    return out
