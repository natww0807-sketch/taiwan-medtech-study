"""Read-only validation of the imported question bank and knowledge modules."""

from __future__ import annotations

import json
from collections import Counter

from study_core import ARCHIVE_DIR, connect, data_dir, stable_question_rank


def validate() -> dict:
    with connect() as conn:
        questions = conn.execute("SELECT * FROM questions ORDER BY rowid").fetchall()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise AssertionError(f"SQLite 完整性檢查失敗: {integrity}")
    if len(questions) != 4800 or len({row["id"] for row in questions}) != 4800:
        raise AssertionError("題數或題目 ID 不符")
    if len({row["source_pdf"] for row in questions}) != 60:
        raise AssertionError("試卷數量不符")
    subjects = Counter(row["subject"] for row in questions)
    if len(subjects) != 6 or set(subjects.values()) != {800}:
        raise AssertionError("科目題數不符")
    modules = Counter((row["subject"], row["topic"], row["subtopic"]) for row in questions)
    if len(modules) != 175 or sum(modules.values()) != 4800:
        raise AssertionError("知識模塊數量或題數不符")
    if sum(row["needs_image"] for row in questions) != 150:
        raise AssertionError("圖表題數不符")
    if sum(row["answer_note"] is not None for row in questions) != 41:
        raise AssertionError("官方更正答案題數不符")
    image_files = {row["image_file"] for row in questions if row["image_file"]}
    image_files.update(name for row in questions for name in json.loads(row["image_extra_json"]))
    if any(not (data_dir() / "images" / name).is_file() for name in image_files):
        raise AssertionError("原卷影像缺檔")
    for row in questions:
        if not (ARCHIVE_DIR / row["source_pdf"]).is_file() or not (ARCHIVE_DIR / row["answer_pdf"]).is_file():
            raise AssertionError(f"原始 PDF 缺檔: {row['id']}")
        options = json.loads(row["options_json"])
        if not row["stem"] or set(options) != set("ABCD") or any(not options[k] for k in "ABCD"):
            raise AssertionError(f"題卡內容缺漏: {row['id']}")
        if (row["parse_warning"] or row["needs_image"]) and not row["image_file"]:
            raise AssertionError(f"需要原卷影像卻未附上: {row['id']}")
    sample = [row["id"] for row in questions[:50]]
    first_order = sorted(sample, key=lambda value: (stable_question_rank(value), value))
    second_order = sorted(reversed(sample), key=lambda value: (stable_question_rank(value), value))
    if first_order != second_order or len(first_order) != len(set(first_order)):
        raise AssertionError("固定混合順序無法重現")
    return {
        "questions": len(questions), "exam_pdfs": 60, "subjects": dict(subjects),
        "modules": len(modules), "image_questions": 150, "corrected_questions": 41,
        "source_image_pages": len(image_files),
        "image_fallback_cards": sum(bool(row["parse_warning"]) for row in questions),
        "sqlite_integrity": integrity,
    }


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, indent=2))
