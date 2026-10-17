"""Repair duplicated PDF text-layer glyphs without touching study history."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime

from import_questions import parse_pdf, read_index
from manual_overrides import MANUAL_OVERRIDES
from study_core import ARCHIVE_DIR, connect, data_dir, db_path


PRESERVED_TABLES = (
    "assignments",
    "attempts",
    "review_state",
    "knowledge_notes",
    "question_supplements",
)


def extracted_questions() -> list[dict]:
    rows, _ = read_index()
    by_pdf = defaultdict(list)
    for row in rows:
        by_pdf[row["source_pdf"]].append(row)

    for relative, group in sorted(by_pdf.items()):
        parsed = parse_pdf(ARCHIVE_DIR / relative, group)
        for row in group:
            stem, options, warnings, _ = parsed[row["number"]]
            override = MANUAL_OVERRIDES.get(row["id"])
            if override:
                stem = override.get("stem", stem)
                options = override["options"]
                warnings = [override["warning"]]
            row["stem"] = stem
            row["options_json"] = json.dumps(options, ensure_ascii=False)
            row["parse_warning"] = "; ".join(warnings) if warnings else None
    return rows


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("questions", *PRESERVED_TABLES)
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write verified text changes to the live database")
    args = parser.parse_args()

    extracted = extracted_questions()
    conn = connect()
    before = table_counts(conn)
    changes = []
    for row in extracted:
        current = conn.execute(
            "SELECT stem,options_json,parse_warning FROM questions WHERE id=?",
            (row["id"],),
        ).fetchone()
        if current is None:
            raise RuntimeError(f"題目不存在：{row['id']}")
        fields = []
        if current["stem"] != row["stem"]:
            fields.append("stem")
        if json.loads(current["options_json"]) != json.loads(row["options_json"]):
            fields.append("options")
        if current["parse_warning"] != row["parse_warning"]:
            fields.append("parse_warning")
        if fields:
            changes.append((row, fields))

    backup = None
    if args.apply and changes:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = data_dir() / "backups" / f"pre-pdf-text-dedupe-{stamp}.sqlite3"
        backup.parent.mkdir(parents=True, exist_ok=True)
        destination = sqlite3.connect(backup)
        conn.backup(destination)
        destination.close()

        with conn:
            conn.executemany(
                "UPDATE questions SET stem=?,options_json=?,parse_warning=? WHERE id=?",
                [
                    (row["stem"], row["options_json"], row["parse_warning"], row["id"])
                    for row, _ in changes
                ],
            )

    after = table_counts(conn)
    if before != after or after["questions"] != 4800:
        raise RuntimeError(f"資料筆數異常：before={before}, after={after}")
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError(f"SQLite 完整性檢查失敗：{integrity}")

    changed_ids = [row["id"] for row, _ in changes]
    subjects = Counter(row["subject"] for row, _ in changes)
    example = conn.execute(
        "SELECT stem,options_json FROM questions WHERE id='113-1-血液血庫-47'"
    ).fetchone()
    print(json.dumps({
        "applied": bool(args.apply),
        "changed_questions": len(changes),
        "changed_by_subject": dict(subjects),
        "changed_ids": changed_ids,
        "backup": str(backup) if backup else None,
        "counts": after,
        "integrity": integrity,
        "example": {
            "stem": example["stem"],
            "options": json.loads(example["options_json"]),
        },
    }, ensure_ascii=False, indent=2))
    conn.close()


if __name__ == "__main__":
    main()
