"""
image_library.py - 图片资产库(SQLite)

这是「生图 -> 人工筛选 -> 爆款/普通图库 -> 反哺风格」的持久化底座。

gallery 的取值:
  unclassified - 刚转存,还没有人工决定
  hot          - 爆款图库,会参与后续风格反馈
  normal       - 普通图库,只归档,不会影响后续风格

设计原则:
  - sha256 唯一去重:同一张图重复注册不产生重复资产。
  - SQLite WAL + 线程复用连接,跟 history.py 保持同一套约定。
  - 数据库不可用时优雅返回 False / [] / None,不拖垮生图主流程。
  - 风格反馈默认要求至少 3 张爆款图,避免一张偶然好图劫持整个风格。
"""
import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

import config

log = logging.getLogger(__name__)

GALLERIES = ("unclassified", "hot", "normal")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS image_assets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path       TEXT NOT NULL,
    file_name       TEXT NOT NULL,
    sha256          TEXT NOT NULL UNIQUE,
    gallery         TEXT NOT NULL DEFAULT 'unclassified',
    style           TEXT NOT NULL DEFAULT '',
    style_label     TEXT NOT NULL DEFAULT '',
    subject         TEXT NOT NULL DEFAULT '',
    selling_points  TEXT NOT NULL DEFAULT '[]',
    prompt          TEXT NOT NULL DEFAULT '',
    aspect_ratio    TEXT NOT NULL DEFAULT '',
    size            TEXT NOT NULL DEFAULT '',
    source          TEXT NOT NULL DEFAULT '',
    bytes           INTEGER NOT NULL DEFAULT 0,
    style_guidance  TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_image_gallery ON image_assets(gallery, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_image_style ON image_assets(style, gallery, updated_at DESC);
"""

_thread_local = threading.local()
_unavailable_logged = False


def db_path() -> str:
    return str(getattr(config, "IMAGE_LIBRARY_DB", "") or "")


def close() -> None:
    conn = getattr(_thread_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
    _thread_local.conn = None
    _thread_local.db_path = None
    _thread_local.schema_ready = False


def _ensure_schema(conn: sqlite3.Connection) -> bool:
    if getattr(_thread_local, "schema_ready", False):
        return True
    try:
        conn.executescript(_SCHEMA)
        _migrate(conn)
        _thread_local.schema_ready = True
        return True
    except sqlite3.Error as e:
        log.warning("图片库建表失败(%s)", e)
        close()
        return False


def _migrate(conn: sqlite3.Connection) -> None:
    """给早期实验库补列,避免换版本后现有图库打不开。"""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(image_assets)")}
    additions = {
        "style_guidance": "TEXT NOT NULL DEFAULT ''",
        "source": "TEXT NOT NULL DEFAULT ''",
        "bytes": "INTEGER NOT NULL DEFAULT 0",
    }
    for name, definition in additions.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE image_assets ADD COLUMN {name} {definition}")
    conn.commit()


def _connect(write: bool = True) -> Optional[sqlite3.Connection]:
    global _unavailable_logged
    path = db_path()
    if not path:
        return None
    cached = getattr(_thread_local, "conn", None)
    if cached is not None and getattr(_thread_local, "db_path", None) == path:
        if write and not _ensure_schema(cached):
            return None
        return cached
    if cached is not None:
        close()
    try:
        if write:
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        conn = sqlite3.connect(path, timeout=30.0)
        if write:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        _thread_local.conn = conn
        _thread_local.db_path = path
        _thread_local.schema_ready = False
        if write and not _ensure_schema(conn):
            return None
        return conn
    except (sqlite3.Error, OSError) as e:
        close()
        if not _unavailable_logged:
            log.warning("图片库不可用(%s),已降级为不记录图库", e)
            _unavailable_logged = True
        return None


def is_available() -> bool:
    return _connect(write=False) is not None


def _json(value: Any, fallback: Any) -> str:
    try:
        return json.dumps(value if value is not None else fallback, ensure_ascii=False)
    except (TypeError, ValueError):
        return json.dumps(fallback, ensure_ascii=False)


def _row(row: sqlite3.Row) -> Dict[str, Any]:
    result = dict(row)
    try:
        result["selling_points"] = json.loads(result.get("selling_points") or "[]")
    except (TypeError, ValueError):
        result["selling_points"] = []
    return result


def add_asset(record: Dict[str, Any], gallery: str = "unclassified") -> Optional[Dict[str, Any]]:
    """注册一个 image_store.save_image() 返回的记录,幂等返回完整资产。"""
    if gallery not in GALLERIES:
        raise ValueError(f"gallery 必须是 {GALLERIES}")
    if not record or not record.get("ok") or not record.get("sha256"):
        return None
    conn = _connect()
    if conn is None:
        return None
    now = time.time()
    meta = record.get("meta") or {}
    values = (
        str(record.get("path") or ""), str(record.get("name") or ""),
        str(record.get("sha256") or ""), gallery,
        str(meta.get("style") or ""), str(meta.get("style_label") or ""),
        str(meta.get("subject") or ""), _json(meta.get("selling_points"), []),
        str(meta.get("prompt") or ""), str(meta.get("aspect_ratio") or ""),
        str(meta.get("size") or ""), str(meta.get("source") or ""),
        int(record.get("bytes") or 0), str(meta.get("style_guidance") or ""),
        now, now,
    )
    try:
        conn.execute(
            "INSERT INTO image_assets "
            "(file_path,file_name,sha256,gallery,style,style_label,subject,selling_points," 
            "prompt,aspect_ratio,size,source,bytes,style_guidance,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(sha256) DO UPDATE SET file_path=excluded.file_path, "
            "file_name=excluded.file_name, updated_at=excluded.updated_at",
            values,
        )
        conn.commit()
        row = conn.execute("SELECT * FROM image_assets WHERE sha256=?", (values[2],)).fetchone()
        return _row(row) if row else None
    except sqlite3.Error as e:
        log.warning("写入图片库失败(%s)", e)
        return None


def add_saved_batch(saved: Dict[str, Any], gallery: str = "unclassified") -> Dict[str, Any]:
    """把 image_store.save_images() 结果批量注册,不影响转存结果。"""
    added: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    meta = saved.get("meta") or {}
    for record in saved.get("saved") or []:
        item = dict(record)
        item["meta"] = meta
        row = add_asset(item, gallery)
        if row:
            added.append(row)
        else:
            failed.append(record)
    return {"ok": bool(added), "added": added, "failed": failed, "count": len(added)}


def get(asset_id: int) -> Optional[Dict[str, Any]]:
    conn = _connect(write=False)
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT * FROM image_assets WHERE id=?", (int(asset_id),)).fetchone()
        return _row(row) if row else None
    except (sqlite3.Error, TypeError, ValueError):
        return None


def list_assets(gallery: Optional[str] = None, style: Optional[str] = None,
                limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    conn = _connect(write=False)
    if conn is None:
        return []
    clauses: List[str] = []
    params: List[Any] = []
    if gallery:
        clauses.append("gallery=?")
        params.append(gallery)
    if style:
        clauses.append("style=?")
        params.append(style)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    try:
        rows = conn.execute(
            "SELECT * FROM image_assets" + where +
            " ORDER BY updated_at DESC LIMIT ? OFFSET ?", params + [max(1, min(int(limit), 500)), max(0, int(offset))]
        ).fetchall()
        return [_row(row) for row in rows]
    except (sqlite3.Error, TypeError, ValueError):
        return []


def mark(asset_id: int, gallery: str, style_guidance: Optional[str] = None
         ) -> Optional[Dict[str, Any]]:
    """人工筛选后标记爆款/普通。标爆款时可填视觉锚点,供后续生图反哺。"""
    if gallery not in GALLERIES:
        raise ValueError(f"gallery 必须是 {GALLERIES}")
    conn = _connect()
    if conn is None:
        return None
    try:
        if style_guidance is None:
            conn.execute("UPDATE image_assets SET gallery=?, updated_at=? WHERE id=?",
                         (gallery, time.time(), int(asset_id)))
        else:
            conn.execute("UPDATE image_assets SET gallery=?, style_guidance=?, updated_at=? WHERE id=?",
                         (gallery, str(style_guidance).strip()[:500], time.time(), int(asset_id)))
        conn.commit()
        return get(asset_id)
    except (sqlite3.Error, TypeError, ValueError):
        return None


def delete(asset_id: int) -> bool:
    """只删图库索引,不删图片文件;图片文件由 image_store/人工清理另行处理。"""
    conn = _connect()
    if conn is None:
        return False
    try:
        cur = conn.execute("DELETE FROM image_assets WHERE id=?", (int(asset_id),))
        conn.commit()
        return cur.rowcount > 0
    except (sqlite3.Error, TypeError, ValueError):
        return False


def stats() -> Dict[str, Any]:
    conn = _connect(write=False)
    result = {"total": 0, "unclassified": 0, "hot": 0, "normal": 0, "styles": {}}
    if conn is None:
        return result
    try:
        for row in conn.execute("SELECT gallery, COUNT(*) AS n FROM image_assets GROUP BY gallery"):
            result[row["gallery"]] = row["n"]
            result["total"] += row["n"]
        for row in conn.execute("SELECT style, gallery, COUNT(*) AS n FROM image_assets GROUP BY style, gallery"):
            style = row["style"] or "(未设置)"
            result["styles"].setdefault(style, {})[row["gallery"]] = row["n"]
    except sqlite3.Error:
        pass
    return result


def style_feedback(style: str, min_samples: Optional[int] = None,
                   max_chars: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    返回爆款风格反馈。只有 hot 样本达到门槛才返回,防止单张偶然好图劫持风格。
    style_guidance 由人工标记时填写,优先于整段 subject-specific prompt。
    """
    min_samples = int(min_samples if min_samples is not None else
                      getattr(config, "IMAGE_HOT_MIN_SAMPLES", 3))
    max_chars = int(max_chars if max_chars is not None else
                    getattr(config, "IMAGE_HOT_FEEDBACK_MAX_CHARS", 500))
    conn = _connect(write=False)
    if conn is None or not style:
        return None
    try:
        rows = conn.execute(
            "SELECT style, style_label, style_guidance, prompt, subject "
            "FROM image_assets WHERE gallery='hot' AND style=? ORDER BY updated_at DESC",
            (style,),
        ).fetchall()
    except sqlite3.Error:
        return None
    if len(rows) < max(1, min_samples):
        return None
    notes: List[str] = []
    for row in rows:
        note = str(row["style_guidance"] or "").strip()
        if note and note not in notes:
            notes.append(note)
    # 没有人工锚点时不把包含具体商品名的历史 prompt 生搬过去,避免污染新商品。
    if not notes:
        return None
    joined = "; ".join(notes)
    joined = joined[:max_chars].rstrip(" ;")
    return {
        "style": style,
        "style_label": rows[0]["style_label"] or style,
        "sample_count": len(rows),
        "guidance": joined,
    }
