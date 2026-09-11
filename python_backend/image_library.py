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
    sha256          TEXT NOT NULL,
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
    updated_at      REAL NOT NULL,
    shop_id         TEXT NOT NULL DEFAULT 'default',
    UNIQUE(shop_id, sha256)
);
"""

# 索引单独建,且**必须**跑在迁移之后 —— 老库里 CREATE TABLE IF NOT EXISTS 会被跳过,
# 紧接着建一个引用新列(shop_id)的索引就会 no such column,整段建表失败,
# 结果是升级后图片库直接不可用。
_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_image_gallery "
    "ON image_assets(shop_id, gallery, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_image_style "
    "ON image_assets(shop_id, style, gallery, updated_at DESC)",
)
_DROP_LEGACY_INDEXES = (
    "DROP INDEX IF EXISTS idx_image_gallery",
    "DROP INDEX IF EXISTS idx_image_style",
)

# 重建表用的临时名。用显式 BEGIN/COMMIT 包住,保证"删旧表 + 改名"这一步是原子的 ——
# 中途崩溃会整体回滚,绝不会留下"数据在新表、旧表已删"的中间态。
_MIG_TABLE = "image_assets_mig"
_REBUILD_FOR_SHOP_UNIQUE = f"""
BEGIN IMMEDIATE;
DROP TABLE IF EXISTS {_MIG_TABLE};
CREATE TABLE {_MIG_TABLE} (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path       TEXT NOT NULL,
    file_name       TEXT NOT NULL,
    sha256          TEXT NOT NULL,
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
    updated_at      REAL NOT NULL,
    shop_id         TEXT NOT NULL DEFAULT 'default',
    UNIQUE(shop_id, sha256)
);
INSERT INTO {_MIG_TABLE}
    (id,file_path,file_name,sha256,gallery,style,style_label,subject,
     selling_points,prompt,aspect_ratio,size,source,bytes,style_guidance,
     created_at,updated_at,shop_id)
SELECT id,file_path,file_name,sha256,gallery,style,style_label,subject,
       selling_points,prompt,aspect_ratio,size,source,bytes,style_guidance,
       created_at,updated_at,COALESCE(NULLIF(shop_id,''),'default')
  FROM image_assets;
DROP TABLE image_assets;
ALTER TABLE {_MIG_TABLE} RENAME TO image_assets;
COMMIT;
"""

_thread_local = threading.local()
_unavailable_logged = False


def db_path() -> str:
    return str(getattr(config, "IMAGE_LIBRARY_DB", "") or "")


def _shop(shop_id: Optional[str] = None) -> str:
    """统一的店铺解析顺序:显式传入 > config.SHOP_ID > 'default'。"""
    return str(shop_id or getattr(config, "SHOP_ID", "") or "default").strip() or "default"


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
    """
    建表 / 补列 / 必要时重建表 / 重建索引。建过了就跳过。

    ⚠️ 读写连接都要走这一步。以前只在 write=True 时跑,结果:图片库 API 是
    每个请求一个线程,如果进程起来后第一个请求是 GET(读),结构升级就不会发生,
    而查询里带了 shop_id 过滤 → no such column → 被 except 吞掉 → 返回空列表。
    用户看到的是"图片库空了",而不是任何报错 —— 这类静默空结果最难查。
    """
    if getattr(_thread_local, "schema_ready", False):
        return True
    try:
        conn.executescript(_SCHEMA)
        _migrate(conn)                    # 补列 + 必要时重建表(唯一键改 (shop_id,sha256))
        _rebuild_indexes_if_stale(conn)   # 老索引不含 shop_id → 必须先 DROP 再建
        for sql in _INDEXES:
            conn.execute(sql)
        conn.commit()
        _thread_local.schema_ready = True
        return True
    except sqlite3.Error as e:
        log.warning("图片库建表失败(%s)", e)
        close()
        return False


def _unique_sha_is_global(conn: sqlite3.Connection) -> bool:
    """
    唯一约束是不是"全局 sha256 唯一"(老结构)。

    老结构下 sha256 是跨店铺唯一的,于是 B 店注册一张 A 店已存过的图时,
    ON CONFLICT 会命中 A 店那一行 —— 更新的是 A 店记录,B 店的图库列表里
    **什么都不会出现**,而且不报任何错。多店铺场景下这是静默丢数据,必须改成
    (shop_id, sha256) 唯一。
    """
    try:
        rows = conn.execute("PRAGMA index_list(image_assets)").fetchall()
    except sqlite3.Error:
        return False
    for row in rows:
        # row = (seq, name, unique, origin, partial);origin 'u' 表示由 UNIQUE 约束生成
        name, unique, origin = row[1], row[2], row[3]
        if not unique or origin != "u":
            continue
        try:
            cols = [r[2] for r in conn.execute(f"PRAGMA index_info({name})").fetchall()]
        except sqlite3.Error:
            continue
        if cols == ["sha256"]:
            return True
    return False


def _index_covers(conn: sqlite3.Connection, index_name: str, column: str) -> bool:
    """索引定义里是否包含某一列。索引不存在时返回 False。"""
    try:
        rows = conn.execute(f"PRAGMA index_info({index_name})").fetchall()
    except sqlite3.Error:
        return False
    return any((r[2] or "") == column for r in rows)


def _rebuild_indexes_if_stale(conn: sqlite3.Connection) -> None:
    """老索引不含 shop_id,而 CREATE INDEX IF NOT EXISTS 不会更新已存在的定义。"""
    if _index_covers(conn, "idx_image_gallery", "shop_id"):
        return
    for sql in _DROP_LEGACY_INDEXES:
        conn.execute(sql)


def _migrate(conn: sqlite3.Connection) -> None:
    """给早期实验库补列,避免换版本后现有图库打不开。"""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(image_assets)")}
    additions = {
        "style_guidance": "TEXT NOT NULL DEFAULT ''",
        "source": "TEXT NOT NULL DEFAULT ''",
        "bytes": "INTEGER NOT NULL DEFAULT 0",
        "shop_id": "TEXT NOT NULL DEFAULT 'default'",
    }
    for name, definition in additions.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE image_assets ADD COLUMN {name} {definition}")
    conn.commit()

    if _unique_sha_is_global(conn):
        # SQLite 不能直接删约束,只能重建表。见 _REBUILD_FOR_SHOP_UNIQUE 的说明。
        conn.executescript(_REBUILD_FOR_SHOP_UNIQUE)
        log.info("图片库已升级:唯一键从「全局 sha256」改为「(shop_id, sha256)」,"
                 "多店铺下同一张图可以各自登记")


def _connect(write: bool = True) -> Optional[sqlite3.Connection]:
    global _unavailable_logged
    path = db_path()
    if not path:
        return None
    cached = getattr(_thread_local, "conn", None)
    if cached is not None and getattr(_thread_local, "db_path", None) == path:
        # 读连接也要跑 _ensure_schema,理由见 _ensure_schema 的注释:
        # 否则"先读后写"时结构升级不发生,按 shop_id 过滤的查询会静默返回空。
        if not _ensure_schema(cached):
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
        if not _ensure_schema(conn):
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


def add_asset(record: Dict[str, Any], gallery: str = "unclassified",
              shop_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """注册一个 image_store.save_image() 返回的记录,幂等返回完整资产。"""
    if gallery not in GALLERIES:
        raise ValueError(f"gallery 必须是 {GALLERIES}")
    if not record or not record.get("ok") or not record.get("sha256"):
        return None
    conn = _connect()
    if conn is None:
        return None
    shop = _shop(shop_id)
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
        now, now, shop,
    )
    try:
        conn.execute(
            "INSERT INTO image_assets "
            "(file_path,file_name,sha256,gallery,style,style_label,subject,selling_points,"
            "prompt,aspect_ratio,size,source,bytes,style_guidance,created_at,updated_at,shop_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            # 冲突目标是 (shop_id, sha256) 而不是 sha256 ——
            # 同一张图在不同店铺里是两条独立资产,不能互相覆盖。
            "ON CONFLICT(shop_id, sha256) DO UPDATE SET file_path=excluded.file_path, "
            "file_name=excluded.file_name, updated_at=excluded.updated_at",
            values,
        )
        conn.commit()
        row = conn.execute("SELECT * FROM image_assets WHERE shop_id=? AND sha256=?",
                           (shop, values[2])).fetchone()
        return _row(row) if row else None
    except sqlite3.Error as e:
        log.warning("写入图片库失败(%s)", e)
        return None


def add_saved_batch(saved: Dict[str, Any], gallery: str = "unclassified",
                    shop_id: Optional[str] = None) -> Dict[str, Any]:
    """把 image_store.save_images() 结果批量注册,不影响转存结果。"""
    added: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    meta = saved.get("meta") or {}
    for record in saved.get("saved") or []:
        item = dict(record)
        item["meta"] = meta
        row = add_asset(item, gallery, shop_id=shop_id)
        if row:
            added.append(row)
        else:
            failed.append(record)
    return {"ok": bool(added), "added": added, "failed": failed, "count": len(added)}


def get(asset_id: int, shop_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _connect(write=False)
    if conn is None:
        return None
    try:
        row = conn.execute("SELECT * FROM image_assets WHERE id=? AND shop_id=?",
                           (int(asset_id), _shop(shop_id))).fetchone()
        return _row(row) if row else None
    except (sqlite3.Error, TypeError, ValueError):
        return None


def list_assets(gallery: Optional[str] = None, style: Optional[str] = None,
                limit: int = 100, offset: int = 0,
                shop_id: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _connect(write=False)
    if conn is None:
        return []
    # shop_id 是**第一道**过滤条件,且永远存在 —— 不会因为调用方没传
    # gallery/style 就退化成"看到所有店铺的图"。
    clauses: List[str] = ["shop_id=?"]
    params: List[Any] = [_shop(shop_id)]
    if gallery:
        clauses.append("gallery=?")
        params.append(gallery)
    if style:
        clauses.append("style=?")
        params.append(style)
    where = " WHERE " + " AND ".join(clauses)
    try:
        rows = conn.execute(
            "SELECT * FROM image_assets" + where +
            " ORDER BY updated_at DESC LIMIT ? OFFSET ?", params + [max(1, min(int(limit), 500)), max(0, int(offset))]
        ).fetchall()
        return [_row(row) for row in rows]
    except (sqlite3.Error, TypeError, ValueError):
        return []


def mark(asset_id: int, gallery: str, style_guidance: Optional[str] = None,
         shop_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """人工筛选后标记爆款/普通。标爆款时可填视觉锚点,供后续生图反哺。"""
    if gallery not in GALLERIES:
        raise ValueError(f"gallery 必须是 {GALLERIES}")
    conn = _connect()
    if conn is None:
        return None
    shop = _shop(shop_id)
    try:
        # WHERE 里必须带 shop_id:否则一个店铺的 id 能把别的店铺的图改掉
        # (id 是全局自增的,猜/撞到别人的 id 完全可能)。
        if style_guidance is None:
            conn.execute("UPDATE image_assets SET gallery=?, updated_at=? WHERE id=? AND shop_id=?",
                         (gallery, time.time(), int(asset_id), shop))
        else:
            conn.execute("UPDATE image_assets SET gallery=?, style_guidance=?, updated_at=? "
                         "WHERE id=? AND shop_id=?",
                         (gallery, str(style_guidance).strip()[:500], time.time(),
                          int(asset_id), shop))
        conn.commit()
        return get(asset_id, shop_id=shop)
    except (sqlite3.Error, TypeError, ValueError):
        return None


def delete(asset_id: int, shop_id: Optional[str] = None) -> bool:
    """只删图库索引,不删图片文件;图片文件由 image_store/人工清理另行处理。"""
    conn = _connect()
    if conn is None:
        return False
    try:
        cur = conn.execute("DELETE FROM image_assets WHERE id=? AND shop_id=?",
                           (int(asset_id), _shop(shop_id)))
        conn.commit()
        return cur.rowcount > 0
    except (sqlite3.Error, TypeError, ValueError):
        return False


def stats(shop_id: Optional[str] = None) -> Dict[str, Any]:
    shop = _shop(shop_id)
    conn = _connect(write=False)
    result = {"shop_id": shop, "total": 0, "unclassified": 0, "hot": 0, "normal": 0, "styles": {}}
    if conn is None:
        return result
    try:
        for row in conn.execute(
                "SELECT gallery, COUNT(*) AS n FROM image_assets WHERE shop_id=? GROUP BY gallery",
                (shop,)):
            result[row["gallery"]] = row["n"]
            result["total"] += row["n"]
        for row in conn.execute(
                "SELECT style, gallery, COUNT(*) AS n FROM image_assets "
                "WHERE shop_id=? GROUP BY style, gallery", (shop,)):
            style = row["style"] or "(未设置)"
            result["styles"].setdefault(style, {})[row["gallery"]] = row["n"]
    except sqlite3.Error:
        pass
    return result


def style_feedback(style: str, min_samples: Optional[int] = None,
                   max_chars: Optional[int] = None,
                   shop_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    返回爆款风格反馈。只有 hot 样本达到门槛才返回,防止单张偶然好图劫持风格。
    style_guidance 由人工标记时填写,优先于整段 subject-specific prompt。

    ⚠️ 必须按店铺过滤:风格反哺是"用这家店验证过的视觉锚点去影响这家店后续的生图",
    把别家店的爆款风格混进来,等于用 A 店的调性去污染 B 店的图。
    """
    min_samples = int(min_samples if min_samples is not None else
                      getattr(config, "IMAGE_HOT_MIN_SAMPLES", 3))
    max_chars = int(max_chars if max_chars is not None else
                    getattr(config, "IMAGE_HOT_FEEDBACK_MAX_CHARS", 500))
    shop = _shop(shop_id)
    conn = _connect(write=False)
    if conn is None or not style:
        return None
    try:
        rows = conn.execute(
            "SELECT style, style_label, style_guidance, prompt, subject "
            "FROM image_assets WHERE shop_id=? AND gallery='hot' AND style=? "
            "ORDER BY updated_at DESC",
            (shop, style),
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
        "shop_id": shop,
        "style": style,
        "style_label": rows[0]["style_label"] or style,
        "sample_count": len(rows),
        "guidance": joined,
    }
