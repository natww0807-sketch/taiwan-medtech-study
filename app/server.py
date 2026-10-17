"""Private LAN study website. Runtime uses only Python's standard library."""

from __future__ import annotations

import argparse
import base64
import binascii
import io
import json
import mimetypes
import secrets
import shutil
import socket
import sqlite3
import tempfile
import time
import zipfile
import ipaddress
from contextlib import contextmanager
from datetime import datetime, timedelta
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from study_core import (
    APP_DIR, ARCHIVE_DIR, RESOURCE_DIR, MODULE_BATCH_SIZE, NOTE_MAX_LENGTH, TZ,
    connect, create_daily_backup, data_dir, db_path, get_active_module, init_db, knowledge_note,
    mark_mastered, module_batch, password_hash, question_public, record_attempt,
    save_knowledge_note, set_active_module, sync_knowledge_notes, today,
    sync_question_supplements, verify_password,
)


APP_VERSION = "1.0.0"
STATIC = RESOURCE_DIR / "static"
SEED_DB = RESOURCE_DIR / "seed" / "study.sqlite3"
MAX_BODY = 20 * 1024 * 1024
MAX_RESTORE_BODY = 250 * 1024 * 1024
MAX_NOTE_IMAGE_SIZE = 5 * 1024 * 1024
MAX_NOTE_IMAGES_PER_QUESTION = 20
NOTE_IMAGE_TYPES = {
    "image/jpeg": ("jpg", lambda data: data.startswith(b"\xff\xd8\xff")),
    "image/png": ("png", lambda data: data.startswith(b"\x89PNG\r\n\x1a\n")),
    "image/webp": ("webp", lambda data: len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"),
}


def config_path() -> Path:
    return data_dir() / "config.json"


def load_config() -> dict:
    defaults = {"network_mode": "local", "port": 8765}
    try:
        value = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return defaults
    mode = value.get("network_mode")
    port = value.get("port")
    return {
        "network_mode": mode if mode in {"local", "external"} else "local",
        "port": port if isinstance(port, int) and 1024 <= port <= 65535 else 8765,
    }


def save_config(network_mode: str) -> dict:
    if network_mode not in {"local", "external"}:
        raise ValueError("連線模式無效")
    value = {**load_config(), "network_mode": network_mode}
    config_path().parent.mkdir(parents=True, exist_ok=True)
    temporary = config_path().with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(config_path())
    return value


def is_loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def setup_required(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM settings WHERE key='auth_hash'").fetchone() is None


def merge_seed_content(conn: sqlite3.Connection, seed_path: Path = SEED_DB) -> None:
    """Refresh shared question content while retaining every user-owned table."""
    if not seed_path.is_file():
        return
    seed = sqlite3.connect(seed_path)
    seed.row_factory = sqlite3.Row
    try:
        question_columns = [row[1] for row in seed.execute("PRAGMA table_info(questions)")]
        update_columns = [name for name in question_columns if name != "id"]
        placeholders = ",".join("?" for _ in question_columns)
        updates = ",".join(f'"{name}"=excluded."{name}"' for name in update_columns)
        rows = [tuple(row[name] for name in question_columns) for row in seed.execute("SELECT * FROM questions")]
        conn.executemany(
            f'INSERT INTO questions({",".join(question_columns)}) VALUES({placeholders}) ON CONFLICT(id) DO UPDATE SET {updates}',
            rows,
        )
        for row in seed.execute("SELECT subject,topic,subtopic,source_markdown,source_hash,source_updated_at FROM knowledge_notes"):
            conn.execute(
                """INSERT INTO knowledge_notes(subject,topic,subtopic,source_markdown,source_hash,source_updated_at)
                   VALUES(?,?,?,?,?,?) ON CONFLICT(subject,topic,subtopic) DO UPDATE SET
                   source_markdown=excluded.source_markdown,source_hash=excluded.source_hash,
                   source_updated_at=excluded.source_updated_at""",
                tuple(row),
            )
        supplement_columns = [row[1] for row in seed.execute("PRAGMA table_info(question_supplements)")]
        supplement_updates = ",".join(
            f'"{name}"=excluded."{name}"' for name in supplement_columns if name != "question_id"
        )
        supplement_rows = [tuple(row[name] for name in supplement_columns) for row in seed.execute("SELECT * FROM question_supplements")]
        conn.executemany(
            f'INSERT INTO question_supplements({",".join(supplement_columns)}) VALUES({",".join("?" for _ in supplement_columns)}) '
            f'ON CONFLICT(question_id) DO UPDATE SET {supplement_updates}',
            supplement_rows,
        )
        conn.execute(
            "INSERT INTO settings(key,value) VALUES('content_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (APP_VERSION,),
        )
    finally:
        seed.close()


def create_pre_update_backup(conn: sqlite3.Connection) -> Path:
    """Snapshot user data before bundled content is merged into an old database."""
    folder = data_dir() / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
    target = folder / f"before-content-update-{stamp}.sqlite3"
    suffix = 1
    while target.exists():
        target = folder / f"before-content-update-{stamp}-{suffix}.sqlite3"
        suffix += 1
    backup = sqlite3.connect(target)
    try:
        conn.backup(backup)
    finally:
        backup.close()
    return target


def prepare_database() -> None:
    target = db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    existing_database = target.exists()
    if not existing_database:
        if not SEED_DB.is_file():
            raise SystemExit(f"找不到乾淨題庫種子：{SEED_DB}")
        shutil.copy2(SEED_DB, target)
    with connect(target) as conn:
        init_db(conn)
        version_row = conn.execute("SELECT value FROM settings WHERE key='content_version'").fetchone()
        current_version = version_row[0] if version_row else None
        if existing_database and current_version != APP_VERSION:
            create_pre_update_backup(conn)
        with conn:
            merge_seed_content(conn)


@contextmanager
def database():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    return conn.execute(sql, params).fetchone()[0]


def summary(conn: sqlite3.Connection) -> dict:
    total = scalar(conn, "SELECT COUNT(*) FROM questions")
    completed = scalar(conn, "SELECT COUNT(DISTINCT question_id) FROM attempts")
    active_wrong = scalar(conn, "SELECT COUNT(*) FROM review_state WHERE mastered=0")
    module_total = scalar(conn, "SELECT COUNT(*) FROM (SELECT 1 FROM questions GROUP BY subject,topic,subtopic)")
    completed_modules = scalar(conn, """SELECT COUNT(*) FROM (
        SELECT q.subject,q.topic,q.subtopic FROM questions q LEFT JOIN attempts a ON a.question_id=q.id
        GROUP BY q.subject,q.topic,q.subtopic HAVING COUNT(DISTINCT a.question_id)=COUNT(*)
    )""")
    active = get_active_module(conn)
    active_row = conn.execute(
        """SELECT COUNT(*) total,COUNT(DISTINCT a.question_id) completed
           FROM questions q LEFT JOIN attempts a ON a.question_id=q.id
           WHERE q.subject=? AND q.topic=? AND q.subtopic=?""",
        (active["subject"], active["topic"], active["subtopic"]),
    ).fetchone()
    recent = conn.execute(
        """SELECT AVG(is_correct),COUNT(*) FROM (SELECT a.is_correct FROM attempts a JOIN questions q ON q.id=a.question_id
           WHERE q.subject=? AND q.topic=? AND q.subtopic=? ORDER BY a.answered_at DESC,a.id DESC LIMIT 20)""",
        (active["subject"], active["topic"], active["subtopic"]),
    ).fetchone()
    active_info = {**active, **dict(active_row), "recent_accuracy": recent[0], "recent_count": recent[1]}
    subjects = [dict(row) for row in conn.execute(
        """SELECT q.subject,COUNT(DISTINCT q.id) total_questions,
             COUNT(DISTINCT CASE WHEN a.question_id IS NOT NULL THEN q.id END) completed_questions,
             COUNT(DISTINCT q.topic || char(31) || q.subtopic) total_modules
           FROM questions q LEFT JOIN attempts a ON a.question_id=q.id
           GROUP BY q.subject ORDER BY MIN(q.rowid)"""
    )]
    # SQLite cannot sum one row per module while also counting questions in the same grouping.
    for item in subjects:
        item["completed_modules"] = scalar(conn, """SELECT COUNT(*) FROM (
            SELECT q.subject,q.topic,q.subtopic FROM questions q LEFT JOIN attempts a ON a.question_id=q.id
            WHERE q.subject=? GROUP BY q.subject,q.topic,q.subtopic HAVING COUNT(DISTINCT a.question_id)=COUNT(*)
        )""", (item["subject"],))
    return {
        "total": total, "completed": completed, "remaining": total-completed,
        "active_wrong": active_wrong, "module_total": module_total,
        "completed_modules": completed_modules, "active_module": active_info, "subjects": subjects,
    }


def categories(conn: sqlite3.Connection) -> list[dict]:
    active = get_active_module(conn)
    rows = [dict(row) for row in conn.execute(
        """SELECT q.subject,q.topic,q.subtopic,COUNT(*) total,
           SUM(CASE WHEN EXISTS (SELECT 1 FROM attempts a WHERE a.question_id=q.id) THEN 1 ELSE 0 END) completed,
           SUM(CASE WHEN EXISTS (SELECT 1 FROM attempts a WHERE a.question_id=q.id AND a.is_correct=0) THEN 1 ELSE 0 END) ever_wrong,
           SUM(CASE WHEN r.mastered=0 THEN 1 ELSE 0 END) active_wrong,
           SUM(CASE WHEN r.mastered=1 THEN 1 ELSE 0 END) mastered,
           CASE WHEN EXISTS (SELECT 1 FROM knowledge_notes k WHERE k.subject=q.subject AND k.topic=q.topic AND k.subtopic=q.subtopic) THEN 1 ELSE 0 END has_knowledge_note
           FROM questions q LEFT JOIN review_state r ON r.question_id=q.id
           GROUP BY q.subject,q.topic,q.subtopic ORDER BY MIN(q.rowid)"""
    )]
    recent = {(row["subject"],row["topic"],row["subtopic"]):(row["accuracy"],row["count"]) for row in conn.execute(
        """WITH ranked AS (SELECT q.subject,q.topic,q.subtopic,a.is_correct,ROW_NUMBER() OVER (
             PARTITION BY q.subject,q.topic,q.subtopic ORDER BY a.answered_at DESC,a.id DESC) rank
             FROM attempts a JOIN questions q ON q.id=a.question_id)
           SELECT subject,topic,subtopic,AVG(is_correct) accuracy,COUNT(*) count FROM ranked WHERE rank<=20
           GROUP BY subject,topic,subtopic"""
    )}
    for row in rows:
        row["recent_accuracy"],row["recent_count"] = recent.get((row["subject"],row["topic"],row["subtopic"]),(None,0))
        row["status"] = "completed" if row["completed"] == row["total"] else "in_progress" if row["completed"] else "not_started"
        row["is_active"] = all(row[key] == active[key] for key in ("subject","topic","subtopic"))
    return rows


def module_batch_payload(conn: sqlite3.Connection, requested: dict[str, str] | None = None) -> dict:
    module = requested or get_active_module(conn)
    if requested:
        module = set_active_module(conn, requested.get("subject", ""), requested.get("topic", ""), requested.get("subtopic", ""))
    ids = module_batch(conn, module["subject"], module["topic"], module["subtopic"])
    modules = categories(conn)
    current_index = next((i for i, item in enumerate(modules) if all(item[k] == module[k] for k in ("subject", "topic", "subtopic"))), -1)
    recommendation = None
    if current_index >= 0 and not ids:
        candidates = modules[current_index + 1:] + modules[:current_index]
        recommendation = next((item for item in candidates if item["status"] != "completed" and item["subject"] == module["subject"] and item["topic"] == module["topic"]), None)
        recommendation = recommendation or next((item for item in candidates if item["status"] != "completed" and item["subject"] == module["subject"]), None)
        recommendation = recommendation or next((item for item in candidates if item["status"] != "completed"), None)
    return {"module": module, "ids": ids, "batch_size": MODULE_BATCH_SIZE,
            "recommendation": {k: recommendation[k] for k in ("subject", "topic", "subtopic")} if recommendation else None}


def question_list(conn: sqlite3.Connection, filters: dict[str, str]) -> list[dict]:
    clauses, values = [], []
    for key in ("subject", "topic", "subtopic"):
        if filters.get(key):
            clauses.append(f"q.{key}=?")
            values.append(filters[key])
    status = filters.get("status", "all")
    if status == "unseen":
        clauses.append("NOT EXISTS(SELECT 1 FROM attempts a WHERE a.question_id=q.id)")
    elif status == "wrong":
        clauses.append("EXISTS(SELECT 1 FROM attempts a WHERE a.question_id=q.id AND a.is_correct=0)")
    elif status in {"due", "active_wrong"}:
        clauses.append("EXISTS(SELECT 1 FROM review_state r WHERE r.question_id=q.id AND r.mastered=0)")
    elif status != "all":
        raise ValueError("篩選狀態無效")
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    return [dict(row) for row in conn.execute(
        f"""SELECT q.id,q.subject,q.topic,q.subtopic,q.year,q.sitting,q.number,q.kind,
            CASE WHEN EXISTS(SELECT 1 FROM attempts a WHERE a.question_id=q.id) THEN 1 ELSE 0 END completed,
            CASE WHEN EXISTS(SELECT 1 FROM attempts a WHERE a.question_id=q.id AND a.is_correct=0) THEN 1 ELSE 0 END ever_wrong
            FROM questions q {where} ORDER BY q.subject,q.topic,q.subtopic,q.year DESC,q.sitting DESC,q.number LIMIT 4800""",
        tuple(values),
    )]


def history(conn: sqlite3.Connection, question_id: str | None = None) -> list[dict]:
    sql = """SELECT a.id,a.question_id,a.selections_json,a.is_correct,a.is_credit,a.mode,a.answered_at,a.note,
             q.subject,q.topic,q.subtopic,q.year,q.sitting,q.number,q.answer_raw
             FROM attempts a JOIN questions q ON q.id=a.question_id"""
    params = ()
    if question_id:
        sql += " WHERE a.question_id=?"
        params = (question_id,)
    sql += " ORDER BY a.answered_at DESC,a.id DESC LIMIT 500"
    result = []
    for row in conn.execute(sql, params):
        item = dict(row)
        item["selections"] = json.loads(item.pop("selections_json"))
        item["is_correct"] = bool(item["is_correct"])
        item["is_credit"] = bool(item["is_credit"])
        result.append(item)
    return result


def note_summaries(conn: sqlite3.Connection) -> list[dict]:
    """Return one current personal note per question with its latest attempt."""
    sql = """SELECT q.id question_id,q.subject,q.topic,q.subtopic,q.year,q.sitting,q.number,q.stem,q.answer_raw,
             n.note,n.updated_at,a.answered_at,a.selections_json,a.is_correct,a.is_credit
             FROM question_notes n
             JOIN questions q ON q.id=n.question_id
             JOIN attempts a ON a.id=(
               SELECT a2.id FROM attempts a2
               WHERE a2.question_id=q.id
               ORDER BY a2.answered_at DESC,a2.id DESC LIMIT 1
             )
             WHERE TRIM(n.note)<>''
             ORDER BY q.subject,q.topic,q.subtopic,q.year DESC,q.sitting DESC,q.number"""
    result = []
    for row in conn.execute(sql):
        item = dict(row)
        item["selections"] = json.loads(item.pop("selections_json"))
        item["is_correct"] = bool(item["is_correct"])
        item["is_credit"] = bool(item["is_credit"])
        result.append(item)
    return result


def export_progress(conn: sqlite3.Connection) -> dict:
    return {
        "format": "medtech-study-progress-v2", "exported_at": datetime.now(TZ).isoformat(),
        "catalog_count": scalar(conn, "SELECT COUNT(*) FROM questions"),
        "preferences": {"active_module": get_active_module(conn)},
        "attempts": [dict(row) for row in conn.execute("SELECT client_id,question_id,selections_json,is_correct,is_credit,mode,answered_at,note FROM attempts ORDER BY id")],
        "review_state": [dict(row) for row in conn.execute("SELECT * FROM review_state ORDER BY question_id")],
        "question_notes": [dict(row) for row in conn.execute("SELECT * FROM question_notes ORDER BY question_id")],
        "knowledge_notes": [dict(row) for row in conn.execute(
            """SELECT subject,topic,subtopic,custom_markdown,user_updated_at
               FROM knowledge_notes WHERE custom_markdown IS NOT NULL ORDER BY subject,topic,subtopic"""
        )],
    }


def note_images_dir() -> Path:
    return data_dir() / "note_images"


def note_attachments(conn: sqlite3.Connection, question_id: str) -> list[dict]:
    return [
        {
            "id": row["id"], "question_id": row["question_id"],
            "name": row["original_name"], "mime_type": row["mime_type"],
            "size_bytes": row["size_bytes"], "created_at": row["created_at"],
            "url": f"/api/note-image/{row['id']}",
            "markdown": f"![{row['original_name']}](/api/note-image/{row['id']})",
        }
        for row in conn.execute(
            """SELECT id,question_id,original_name,mime_type,size_bytes,created_at
               FROM note_attachments WHERE question_id=? ORDER BY created_at,id""",
            (question_id,),
        )
    ]


def decode_note_image(mime_type: str, encoded: str) -> tuple[bytes, str]:
    if mime_type not in NOTE_IMAGE_TYPES or not isinstance(encoded, str):
        raise ValueError("只接受 JPG、PNG 或 WebP 圖片")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("圖片資料無效") from None
    extension, signature_ok = NOTE_IMAGE_TYPES[mime_type]
    if not data or len(data) > MAX_NOTE_IMAGE_SIZE:
        raise ValueError("每張圖片不得超過 5 MB")
    if not signature_ok(data):
        raise ValueError("圖片內容與檔案類型不符")
    return data, extension


def save_note_image(conn: sqlite3.Connection, question_id: str, original_name: str, mime_type: str, encoded: str) -> dict:
    if not conn.execute("SELECT 1 FROM questions WHERE id=?", (question_id,)).fetchone():
        raise ValueError("找不到題目")
    if scalar(conn, "SELECT COUNT(*) FROM note_attachments WHERE question_id=?", (question_id,)) >= MAX_NOTE_IMAGES_PER_QUESTION:
        raise ValueError("每題最多保存 20 張筆記圖片")
    data, extension = decode_note_image(mime_type, encoded)
    attachment_id = secrets.token_hex(16)
    storage_name = f"{attachment_id}.{extension}"
    safe_name = Path(str(original_name or f"筆記圖片.{extension}")).name[:200]
    folder = note_images_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / storage_name
    target.write_bytes(data)
    stamp = datetime.now(TZ).isoformat()
    try:
        with conn:
            conn.execute(
                """INSERT INTO note_attachments(id,question_id,original_name,storage_name,mime_type,size_bytes,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (attachment_id, question_id, safe_name, storage_name, mime_type, len(data), stamp),
            )
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return next(item for item in note_attachments(conn, question_id) if item["id"] == attachment_id)


def delete_note_image(conn: sqlite3.Connection, attachment_id: str) -> dict:
    row = conn.execute(
        "SELECT question_id,storage_name FROM note_attachments WHERE id=?", (attachment_id,)
    ).fetchone()
    if not row:
        raise ValueError("找不到筆記圖片")
    target = note_images_dir() / row["storage_name"]
    if target.is_file():
        target.unlink()
    with conn:
        conn.execute("DELETE FROM note_attachments WHERE id=?", (attachment_id,))
    return {"ok": True, "question_id": row["question_id"]}


def export_full_backup(conn: sqlite3.Connection) -> bytes:
    rows = [dict(row) for row in conn.execute(
        """SELECT id,question_id,original_name,storage_name,mime_type,size_bytes,created_at
           FROM note_attachments ORDER BY created_at,id"""
    )]
    manifest = {
        "format": "medtech-study-full-backup-v2",
        "exported_at": datetime.now(TZ).isoformat(),
        "progress": export_progress(conn),
        "attachments": rows,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json_bytes(manifest))
        for row in rows:
            source = note_images_dir() / row["storage_name"]
            if not source.is_file() or source.stat().st_size != row["size_bytes"]:
                raise ValueError(f"筆記圖片遺失或大小不符：{row['original_name']}")
            archive.write(source, f"note_images/{row['storage_name']}")
    return output.getvalue()


def save_note(conn: sqlite3.Connection, question_id: str, note: str, attempt_id: int | None = None) -> None:
    if not conn.execute("SELECT 1 FROM questions WHERE id=?", (question_id,)).fetchone():
        raise ValueError("找不到題目")
    if attempt_id is not None:
        if type(attempt_id) is not int or not conn.execute(
            "SELECT 1 FROM attempts WHERE id=? AND question_id=?", (attempt_id, question_id)
        ).fetchone():
            raise ValueError("找不到這次作答")
    if not isinstance(note, str) or len(note) > NOTE_MAX_LENGTH:
        raise ValueError("筆記內容過長")
    with conn:
        conn.execute(
            "INSERT INTO question_notes(question_id,note,updated_at) VALUES(?,?,?) ON CONFLICT(question_id) DO UPDATE SET note=excluded.note,updated_at=excluded.updated_at",
            (question_id, note, datetime.now(TZ).isoformat()),
        )
        if attempt_id is not None:
            conn.execute("UPDATE attempts SET note=? WHERE id=?", (note, attempt_id))


def restore_progress(conn: sqlite3.Connection, payload: dict) -> dict:
    if payload.get("format") not in {"medtech-study-progress-v1", "medtech-study-progress-v2"} or payload.get("catalog_count") != 4800:
        raise ValueError("備份格式或題庫版本不符")
    attempts = payload.get("attempts")
    states = payload.get("review_state")
    notes = payload.get("question_notes")
    knowledge = payload.get("knowledge_notes")
    active_module = (payload.get("preferences") or {}).get("active_module")
    if not isinstance(attempts, list) or not isinstance(states, list) or not isinstance(notes, list) or len(attempts) > 100000:
        raise ValueError("備份內容無效")
    if knowledge is not None and (not isinstance(knowledge, list) or len(knowledge) > 1000):
        raise ValueError("知識筆記備份內容無效")
    valid_ids = {row[0] for row in conn.execute("SELECT id FROM questions")}
    ids = [a.get("client_id") for a in attempts]
    if len(ids) != len(set(ids)) or any(a.get("question_id") not in valid_ids for a in attempts + states + notes):
        raise ValueError("備份題目或作答識別碼無效")
    valid_knowledge = {
        (row[0], row[1], row[2])
        for row in conn.execute("SELECT subject,topic,subtopic FROM knowledge_notes")
    }
    if knowledge is not None and any(
        (item.get("subject"), item.get("topic"), item.get("subtopic")) not in valid_knowledge
        or not isinstance(item.get("custom_markdown"), str)
        or len(item["custom_markdown"]) > 200_000
        for item in knowledge
    ):
        raise ValueError("知識筆記備份分類無效")
    backup_dir = data_dir() / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    pre_restore = backup_dir / f"pre-restore-{datetime.now(TZ).strftime('%Y%m%d-%H%M%S')}.sqlite3"
    backup = sqlite3.connect(pre_restore)
    try:
        conn.backup(backup)
    finally:
        backup.close()
    with conn:
        conn.execute("DELETE FROM attempts")
        conn.execute("DELETE FROM review_state")
        conn.execute("DELETE FROM question_notes")
        conn.executemany(
            "INSERT INTO attempts(client_id,question_id,selections_json,is_correct,is_credit,mode,answered_at,note) VALUES(?,?,?,?,?,?,?,?)",
            [(a["client_id"], a["question_id"], a["selections_json"], a["is_correct"], a["is_credit"], a["mode"], a["answered_at"], a.get("note", "")) for a in attempts],
        )
        conn.executemany(
            "INSERT INTO review_state(question_id,stage,due_date,mastered,updated_at) VALUES(?,?,?,?,?)",
            [(a["question_id"], a["stage"], a.get("due_date"), a["mastered"], a["updated_at"]) for a in states],
        )
        conn.executemany(
            "INSERT INTO question_notes(question_id,note,updated_at) VALUES(?,?,?)",
            [(a["question_id"], a["note"], a["updated_at"]) for a in notes],
        )
        if knowledge is not None:
            conn.execute("UPDATE knowledge_notes SET custom_markdown=NULL,user_updated_at=NULL")
            conn.executemany(
                """UPDATE knowledge_notes SET custom_markdown=?,user_updated_at=?
                   WHERE subject=? AND topic=? AND subtopic=?""",
                [(a["custom_markdown"], a.get("user_updated_at"), a["subject"], a["topic"], a["subtopic"]) for a in knowledge],
            )
        conn.execute("DELETE FROM settings WHERE key='active_module'")
    if active_module:
        set_active_module(conn, active_module.get("subject", ""), active_module.get("topic", ""), active_module.get("subtopic", ""))
    else:
        get_active_module(conn)
    return {"attempts": len(attempts), "pre_restore_backup": str(pre_restore)}


def restore_full_backup(conn: sqlite3.Connection, body: bytes) -> dict:
    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
    except (zipfile.BadZipFile, OSError):
        raise ValueError("完整備份 ZIP 無法讀取") from None
    with archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)) or "manifest.json" not in names:
            raise ValueError("完整備份檔案結構無效")
        if any(name.startswith("/") or ".." in Path(name).parts for name in names):
            raise ValueError("完整備份含有不安全路徑")
        if sum(info.file_size for info in infos) > MAX_RESTORE_BODY:
            raise ValueError("完整備份解壓後超過 250 MB")
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("完整備份清單無效") from None
        if manifest.get("format") not in {"medtech-study-full-backup-v1", "medtech-study-full-backup-v2"}:
            raise ValueError("完整備份版本不符")
        attachments = manifest.get("attachments")
        if not isinstance(attachments, list) or len(attachments) > 96_000:
            raise ValueError("筆記圖片清單無效")
        valid_ids = {row[0] for row in conn.execute("SELECT id FROM questions")}
        seen_ids, seen_files, prepared = set(), set(), []
        for item in attachments:
            if not isinstance(item, dict):
                raise ValueError("筆記圖片資料無效")
            attachment_id = item.get("id")
            question_id = item.get("question_id")
            storage_name = item.get("storage_name")
            mime_type = item.get("mime_type")
            original_name = item.get("original_name")
            size_bytes = item.get("size_bytes")
            created_at = item.get("created_at")
            if (
                not isinstance(attachment_id, str) or len(attachment_id) != 32
                or any(ch not in "0123456789abcdef" for ch in attachment_id)
                or attachment_id in seen_ids or question_id not in valid_ids
                or not isinstance(storage_name, str) or Path(storage_name).name != storage_name
                or storage_name in seen_files or not isinstance(original_name, str)
                or not isinstance(created_at, str) or not isinstance(size_bytes, int)
                or size_bytes < 1 or size_bytes > MAX_NOTE_IMAGE_SIZE or mime_type not in NOTE_IMAGE_TYPES
            ):
                raise ValueError("筆記圖片中繼資料無效")
            extension = NOTE_IMAGE_TYPES[mime_type][0]
            if storage_name != f"{attachment_id}.{extension}":
                raise ValueError("筆記圖片檔名無效")
            archive_name = f"note_images/{storage_name}"
            if archive_name not in names:
                raise ValueError("完整備份缺少筆記圖片")
            data = archive.read(archive_name)
            if len(data) != size_bytes or not NOTE_IMAGE_TYPES[mime_type][1](data):
                raise ValueError("備份圖片內容或大小不符")
            seen_ids.add(attachment_id)
            seen_files.add(storage_name)
            prepared.append((item, data))
        counts: dict[str, int] = {}
        for item, _ in prepared:
            counts[item["question_id"]] = counts.get(item["question_id"], 0) + 1
        if any(count > MAX_NOTE_IMAGES_PER_QUESTION for count in counts.values()):
            raise ValueError("備份中單題圖片超過 20 張")

    target = note_images_dir()
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="note-images-restore-", dir=target.parent))
    for item, data in prepared:
        (stage / item["storage_name"]).write_bytes(data)
    backup_dir = data_dir() / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
    image_backup = backup_dir / f"pre-restore-note-images-{stamp}.zip"
    if target.is_dir():
        with zipfile.ZipFile(image_backup, "w", compression=zipfile.ZIP_DEFLATED) as old_archive:
            for source in target.iterdir():
                if source.is_file():
                    old_archive.write(source, source.name)
    else:
        with zipfile.ZipFile(image_backup, "w"):
            pass

    result = restore_progress(conn, manifest.get("progress", {}))
    old = target.with_name(f"note_images.pre-restore-{stamp}")
    try:
        if old.exists():
            shutil.rmtree(old)
        if target.exists():
            target.rename(old)
        stage.rename(target)
        with conn:
            conn.execute("DELETE FROM note_attachments")
            conn.executemany(
                """INSERT INTO note_attachments(id,question_id,original_name,storage_name,mime_type,size_bytes,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                [
                    (
                        item["id"], item["question_id"], item["original_name"][:200],
                        item["storage_name"], item["mime_type"], item["size_bytes"], item["created_at"],
                    )
                    for item, _ in prepared
                ],
            )
    except Exception:
        if target.exists():
            shutil.rmtree(target)
        if old.exists():
            old.rename(target)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    if old.exists():
        shutil.rmtree(old)
    result.update(attachments=len(prepared), pre_restore_image_backup=str(image_backup))
    return result


class StudyServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler):
        super().__init__(address, handler)
        self.sessions: dict[str, float] = {}
        self.failures: dict[str, tuple[int, float]] = {}


class Handler(BaseHTTPRequestHandler):
    server: StudyServer

    def log_message(self, format, *args):
        print(f"[{datetime.now(TZ).isoformat(timespec='seconds')}] {self.address_string()} {format % args}")

    def send_data(self, status: int, body: bytes, mime: str, filename: str | None = None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-src 'self'; object-src 'self'; base-uri 'self'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, value, status: int = 200):
        self.send_data(status, json_bytes(value), "application/json; charset=utf-8")

    def fail(self, message: str, status: int = 400):
        self.send_json({"error": message}, status)

    def authorized(self) -> bool:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return False
        token = cookie.get("sid")
        if not token:
            return False
        expiry = self.server.sessions.get(token.value, 0)
        if expiry <= time.time():
            self.server.sessions.pop(token.value, None)
            return False
        return True

    def require_auth(self) -> bool:
        if not self.authorized():
            self.fail("請先登入", 401)
            return False
        return True

    def read_json(self):
        size = int(self.headers.get("Content-Length", "0"))
        if size < 1 or size > MAX_BODY:
            raise ValueError("資料大小無效")
        return json.loads(self.rfile.read(size))

    def read_bytes(self, limit: int = MAX_BODY) -> bytes:
        size = int(self.headers.get("Content-Length", "0"))
        if size < 1 or size > limit:
            raise ValueError("資料大小無效")
        return self.rfile.read(size)

    def origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return origin in {f"http://{self.headers.get('Host')}", f"https://{self.headers.get('Host')}"}

    def do_GET(self):
        parsed = urlparse(self.path)
        route = unquote(parsed.path)
        if route == "/api/status":
            with database() as conn:
                needs_setup = setup_required(conn)
            self.send_json({
                "ready": db_path().is_file(), "authenticated": self.authorized(),
                "setup_required": needs_setup, "network_mode": load_config()["network_mode"],
                "version": APP_VERSION,
            })
            return
        if route in {"/", "/index.html", "/app.js", "/style.css"}:
            name = "index.html" if route == "/" else route.lstrip("/")
            target = STATIC / name
            mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
            self.send_data(200, target.read_bytes(), mime + ("; charset=utf-8" if mime.startswith("text/") or mime == "application/javascript" else ""))
            return
        if not self.require_auth():
            return
        try:
            if route.startswith("/api/note-image/"):
                attachment_id = route[len("/api/note-image/"):]
                if len(attachment_id) != 32 or any(ch not in "0123456789abcdef" for ch in attachment_id):
                    raise ValueError("筆記圖片識別碼無效")
                with database() as conn:
                    row = conn.execute(
                        "SELECT storage_name,mime_type,size_bytes FROM note_attachments WHERE id=?", (attachment_id,)
                    ).fetchone()
                if not row:
                    raise ValueError("找不到筆記圖片")
                target = note_images_dir() / row["storage_name"]
                if not target.is_file() or target.stat().st_size != row["size_bytes"]:
                    raise ValueError("筆記圖片檔案遺失")
                self.send_data(200, target.read_bytes(), row["mime_type"])
                return
            if route.startswith("/api/image/"):
                name = route.rsplit("/", 1)[-1]
                if not name.endswith(".jpg") or "/" in name or ".." in name:
                    raise ValueError("圖片路徑無效")
                with database() as conn:
                    if not conn.execute("SELECT 1 FROM questions WHERE image_file=? OR image_extra_json LIKE ? LIMIT 1", (name, f"%{name}%")).fetchone():
                        raise ValueError("找不到圖片")
                target = data_dir() / "images" / name
                if not target.is_file():
                    target = RESOURCE_DIR / "images" / name
                self.send_data(200, target.read_bytes(), "image/jpeg")
                return
            if route.startswith("/api/source/"):
                qid = route[len("/api/source/"):]
                with database() as conn:
                    row = conn.execute("SELECT source_pdf FROM questions WHERE id=?", (qid,)).fetchone()
                if not row:
                    raise ValueError("找不到題目")
                path = (ARCHIVE_DIR / row[0]).resolve()
                if not path.is_relative_to(ARCHIVE_DIR) or not path.is_file():
                    raise ValueError("原卷不存在")
                self.send_data(200, path.read_bytes(), "application/pdf")
                return
            with database() as conn:
                if route == "/api/summary":
                    self.send_json(summary(conn))
                elif route == "/api/categories":
                    self.send_json(categories(conn))
                elif route == "/api/module-batch":
                    query = {k: v[0] for k, v in parse_qs(parsed.query).items() if v}
                    requested = {k: query[k] for k in ("subject", "topic", "subtopic")} if all(query.get(k) for k in ("subject", "topic", "subtopic")) else None
                    self.send_json(module_batch_payload(conn, requested))
                elif route == "/api/knowledge-note":
                    query = {k: v[0] for k, v in parse_qs(parsed.query).items() if v}
                    self.send_json(knowledge_note(
                        conn, query.get("subject", ""), query.get("topic", ""), query.get("subtopic", "")
                    ))
                elif route == "/api/app-settings":
                    self.send_json({**load_config(), "restart_required": False})
                elif route == "/api/list":
                    query = {k: v[0] for k, v in parse_qs(parsed.query).items() if v}
                    self.send_json(question_list(conn, query))
                elif route == "/api/history":
                    query = parse_qs(parsed.query)
                    self.send_json(history(conn, query.get("question_id", [None])[0]))
                elif route == "/api/note-summaries":
                    self.send_json(note_summaries(conn))
                elif route == "/api/export":
                    body = json_bytes(export_progress(conn))
                    self.send_data(200, body, "application/json; charset=utf-8", f"medtech-progress-{today().isoformat()}.json")
                elif route == "/api/export-full":
                    body = export_full_backup(conn)
                    self.send_data(200, body, "application/zip", f"medtech-full-backup-{today().isoformat()}.zip")
                elif route.startswith("/api/question/"):
                    qid = route[len("/api/question/"):]
                    row = conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
                    if not row:
                        raise ValueError("找不到題目")
                    count = scalar(conn, "SELECT COUNT(*) FROM attempts WHERE question_id=?", (qid,))
                    state = conn.execute("SELECT stage,due_date,mastered FROM review_state WHERE question_id=?", (qid,)).fetchone()
                    note = conn.execute("SELECT note FROM question_notes WHERE question_id=?", (qid,)).fetchone()
                    has_knowledge_note = conn.execute(
                        "SELECT 1 FROM knowledge_notes WHERE subject=? AND topic=? AND subtopic=?",
                        (row["subject"], row["topic"], row["subtopic"]),
                    ).fetchone()
                    item = question_public(row, count > 0)
                    item.update(
                        attempts=count, review_state=dict(state) if state else None,
                        note=note[0] if note else "",
                        has_knowledge_note=bool(has_knowledge_note),
                        source_url=f"/api/source/{qid}#page={row['source_page']}",
                        image_url=f"/api/image/{row['image_file']}" if row["image_file"] else None,
                        image_urls=[f"/api/image/{name}" for name in ([row["image_file"]] + json.loads(row["image_extra_json"])) if name],
                        note_attachments=note_attachments(conn, qid),
                    )
                    self.send_json(item)
                else:
                    self.fail("找不到頁面", 404)
        except (ValueError, OSError, sqlite3.Error) as exc:
            self.fail(str(exc), 400)

    def do_POST(self):
        if not self.origin_ok():
            self.fail("來源網站不符", 403)
            return
        route = urlparse(self.path).path
        try:
            if route == "/api/restore-full":
                if not self.require_auth():
                    return
                if self.headers.get("X-Restore-Confirm") != "RESTORE":
                    raise ValueError("請確認還原")
                body = self.read_bytes(MAX_RESTORE_BODY)
                with database() as conn:
                    self.send_json(restore_full_backup(conn, body))
                return
            payload = self.read_json()
            if route == "/api/setup":
                if not is_loopback(self.client_address[0]):
                    self.fail("首次設定只能在安裝這套程式的電腦進行", 403)
                    return
                password = str(payload.get("password", ""))
                if len(password) < 8:
                    raise ValueError("密碼至少需要 8 個字元")
                with database() as conn:
                    if not setup_required(conn):
                        raise ValueError("已完成首次設定")
                    with conn:
                        conn.execute("INSERT INTO settings(key,value) VALUES('auth_hash',?)", (password_hash(password),))
                self.send_json({"ok": True})
                return
            if route == "/api/login":
                ip = self.client_address[0]
                count, until = self.server.failures.get(ip, (0, 0))
                if until > time.time():
                    self.fail("請稍後再試", 429)
                    return
                with database() as conn:
                    row = conn.execute("SELECT value FROM settings WHERE key='auth_hash'").fetchone()
                if not row or not verify_password(str(payload.get("password", "")), row[0]):
                    count += 1
                    self.server.failures[ip] = (count, time.time() + 60 if count >= 5 else 0)
                    self.fail("密碼錯誤", 401)
                    return
                self.server.failures.pop(ip, None)
                token = secrets.token_urlsafe(32)
                self.server.sessions[token] = time.time() + 12 * 3600
                body = json_bytes({"ok": True})
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Set-Cookie", f"sid={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if not self.require_auth():
                return
            with database() as conn:
                if route == "/api/attempt":
                    result = record_attempt(
                        conn, str(payload.get("question_id", "")), payload.get("selections", []),
                        str(payload.get("mode", "new")), str(payload.get("note", "")),
                        str(payload.get("client_id", "")),
                    )
                    self.send_json(result)
                elif route == "/api/active-module":
                    module = set_active_module(conn, str(payload.get("subject", "")), str(payload.get("topic", "")), str(payload.get("subtopic", "")))
                    self.send_json({"ok": True, "module": module})
                elif route == "/api/app-settings":
                    value = save_config(str(payload.get("network_mode", "")))
                    self.send_json({**value, "restart_required": True})
                elif route == "/api/review-mastered":
                    self.send_json(mark_mastered(conn, str(payload.get("question_id", "")), bool(payload.get("mastered", True))))
                elif route == "/api/note":
                    qid = str(payload.get("question_id", ""))
                    save_note(conn, qid, str(payload.get("note", "")), payload.get("attempt_id"))
                    self.send_json({"ok": True})
                elif route == "/api/knowledge-note":
                    self.send_json(save_knowledge_note(
                        conn,
                        str(payload.get("subject", "")),
                        str(payload.get("topic", "")),
                        str(payload.get("subtopic", "")),
                        payload.get("markdown", ""),
                    ))
                elif route == "/api/note-image":
                    item = save_note_image(
                        conn,
                        str(payload.get("question_id", "")),
                        str(payload.get("name", "")),
                        str(payload.get("mime_type", "")),
                        payload.get("data_base64", ""),
                    )
                    self.send_json(item)
                elif route == "/api/note-image/delete":
                    if payload.get("confirm") != "DELETE":
                        raise ValueError("請確認刪除")
                    self.send_json(delete_note_image(conn, str(payload.get("attachment_id", ""))))
                elif route == "/api/restore":
                    if payload.get("confirm") != "RESTORE":
                        raise ValueError("請確認還原")
                    result = restore_progress(conn, payload.get("backup", {}))
                    self.send_json(result)
                elif route == "/api/logout":
                    cookie = SimpleCookie()
                    cookie.load(self.headers.get("Cookie", ""))
                    if cookie.get("sid"):
                        self.server.sessions.pop(cookie["sid"].value, None)
                    body = json_bytes({"ok": True})
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Set-Cookie", "sid=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.fail("找不到頁面", 404)
        except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError, sqlite3.Error) as exc:
            self.fail(str(exc), 400)


def initialize_password() -> str | None:
    with database() as conn:
        init_db(conn)
        row = conn.execute("SELECT value FROM settings WHERE key='auth_hash'").fetchone()
        if row:
            return None
        password = secrets.token_urlsafe(12)
        with conn:
            conn.execute("INSERT INTO settings(key,value) VALUES('auth_hash',?)", (password_hash(password),))
        return password


def local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))
            return sock.getsockname()[0]
    except OSError:
        return "本機區網 IP"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=None, help="Override the saved network mode")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--reset-password", action="store_true")
    args = parser.parse_args(argv)
    prepare_database()
    if args.reset_password:
        with database() as conn:
            password = secrets.token_urlsafe(12)
            with conn:
                conn.execute("INSERT INTO settings(key,value) VALUES('auth_hash',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (password_hash(password),))
        print(f"新的登入密碼：{password}")
        return
    with database() as conn:
        init_db(conn)
        imported = sync_knowledge_notes(conn)
        if imported != 85:
            raise SystemExit(f"知識筆記分類數量異常：{imported}")
        supplement_count = sync_question_supplements(conn)
        if supplement_count != 4800:
            raise SystemExit(f"逐題背景補充數量異常：{supplement_count}")
        count = scalar(conn, "SELECT COUNT(*) FROM questions")
        if count != 4800:
            raise SystemExit(f"題庫數量異常：{count}")
        create_daily_backup(conn, today())
    config = load_config()
    host = args.host or ("127.0.0.1" if config["network_mode"] == "local" else "0.0.0.0")
    port = args.port or config["port"]
    server = StudyServer((host, port), Handler)
    print(f"電腦：http://localhost:{port}")
    if host == "0.0.0.0":
        print(f"同 Wi-Fi／Tailscale：http://{local_ip()}:{port}")
    print(f"題卡與作答資料：{data_dir()}")
    print("按 Control-C 結束服務。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
