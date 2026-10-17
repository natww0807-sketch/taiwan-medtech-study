from __future__ import annotations

import re
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = (
    "/Users/" + "change",
    "natalie" + "wong",
    ".".join(("100", "78", "186", "14")),
    ".".join(("192", "168", "1", "110")),
)
IGNORED_DIRECTORIES = {".git", ".venv", "build", "dist", "release", "__pycache__"}


def main() -> None:
    findings = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if (
            not path.is_file()
            or any(part in IGNORED_DIRECTORIES for part in relative.parts)
            or path.suffix.lower() in {".pdf", ".xlsx", ".jpg", ".sqlite3"}
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for value in FORBIDDEN:
            if value.lower() in text.lower():
                findings.append(f"{path.relative_to(ROOT)}: {value}")
    if findings:
        raise SystemExit("發現私人資訊：\n" + "\n".join(findings))

    database = ROOT / "app" / "seed" / "study.sqlite3"
    with sqlite3.connect(database) as conn:
        expected = {
            "questions": 4800,
            "knowledge_notes": 85,
            "question_supplements": 4800,
            "attempts": 0,
            "review_state": 0,
            "question_notes": 0,
            "note_attachments": 0,
            "settings": 0,
        }
        for table, count in expected.items():
            actual = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if actual != count:
                raise SystemExit(f"{table}: expected {count}, got {actual}")
        modules = conn.execute("SELECT COUNT(*) FROM (SELECT 1 FROM questions GROUP BY subject,topic,subtopic)").fetchone()[0]
        if modules != 175:
            raise SystemExit(f"modules: expected 175, got {modules}")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise SystemExit("SQLite integrity check failed")
    print("public release check: OK")


if __name__ == "__main__":
    main()
