"""Build the public, user-data-free database and rendered question images."""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
sys.path.insert(0, str(APP))


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="medtech-seed-") as folder:
        target = Path(folder)
        os.environ["MEDTECH_STUDY_DATA_DIR"] = str(target)
        from import_questions import import_bank
        from study_core import connect, sync_knowledge_notes, sync_question_supplements

        report = import_bank(target, render_images=True)
        if report["questions"] != 4800:
            raise SystemExit(f"題目數量異常：{report['questions']}")
        database = target / "study.sqlite3"
        with connect(database) as conn:
            notes = sync_knowledge_notes(conn)
            supplements = sync_question_supplements(conn)
            modules = conn.execute(
                "SELECT COUNT(*) FROM (SELECT 1 FROM questions GROUP BY subject,topic,subtopic)"
            ).fetchone()[0]
            if (modules, notes, supplements) != (175, 85, 4800):
                raise SystemExit(f"內容數量異常：modules={modules}, notes={notes}, supplements={supplements}")
            with conn:
                for table in ("attempts", "review_state", "question_notes", "note_attachments", "settings"):
                    conn.execute(f"DELETE FROM {table}")
            conn.execute("VACUUM")
            for table in ("attempts", "review_state", "question_notes", "note_attachments", "settings"):
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                if count:
                    raise SystemExit(f"公開種子仍含有使用者資料：{table}={count}")

        seed_dir = APP / "seed"
        image_dir = APP / "images"
        seed_dir.mkdir(parents=True, exist_ok=True)
        if image_dir.exists():
            shutil.rmtree(image_dir)
        shutil.copytree(target / "images", image_dir)
        shutil.copy2(database, seed_dir / "study.sqlite3")
        print(f"seed={seed_dir / 'study.sqlite3'}")
        print(f"images={len(list(image_dir.glob('*.jpg')))}")


if __name__ == "__main__":
    main()
