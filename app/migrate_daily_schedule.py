"""Move the existing eight-round plan to consecutive days without touching attempts."""

from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime, timedelta

from study_core import (
    DAILY_NEW_LIMIT, PLANNED_DAYS, START_DATE, TZ,
    connect, data_dir, next_study_day,
)


LEGACY_SUBJECT_WEEKDAYS = (1, 3, 4, 5, 6, 0)
REVIEW_DELAYS = (1, 3, 7, 14)


def migrate() -> dict:
    conn = connect()
    try:
        subjects = [row[0] for row in conn.execute(
            "SELECT subject FROM questions GROUP BY subject ORDER BY MIN(rowid)"
        )]
        rows = conn.execute(
            "SELECT a.question_id,a.base_date,a.week_number,a.position,a.sequence,q.subject "
            "FROM assignments a JOIN questions q ON q.id=a.question_id"
        ).fetchall()
        if len(subjects) != 6 or len(rows) != PLANNED_DAYS * DAILY_NEW_LIMIT:
            raise ValueError("題庫或排程題數不符，未更動資料")
        subject_round_counts = Counter((row["subject"], row["week_number"]) for row in rows)
        if any(subject_round_counts[(subject, week)] != DAILY_NEW_LIMIT
               for subject in subjects for week in range(1, 9)):
            raise ValueError("每科每輪題數不符，未更動資料")
        if any(sorted(row["position"] for row in rows if row["subject"] == subject and row["week_number"] == week)
               != list(range(1, DAILY_NEW_LIMIT + 1))
               for subject in subjects for week in range(1, 9)):
            raise ValueError("題目位置有缺漏或重複，未更動資料")

        old_days = [START_DATE + timedelta(days=n) for n in range(56)
                    if (START_DATE + timedelta(days=n)).weekday() != 2]
        old_index = {day.isoformat(): index for index, day in enumerate(old_days)}
        old_subject_days = {
            subject: [day for day in old_days if day.weekday() == LEGACY_SUBJECT_WEEKDAYS[index]]
            for index, subject in enumerate(subjects)
        }
        new_values = []
        legacy_matches = True
        current_matches = True
        for row in rows:
            subject_index = subjects.index(row["subject"])
            round_index = row["week_number"] - 1
            if not 0 <= round_index < 8:
                raise ValueError("輪次超出範圍，未更動資料")
            old_day = old_subject_days[row["subject"]][round_index].isoformat()
            old_sequence = old_index[old_day] * DAILY_NEW_LIMIT + row["position"]
            new_day_index = round_index * len(subjects) + subject_index
            new_day = (START_DATE + timedelta(days=new_day_index)).isoformat()
            new_sequence = new_day_index * DAILY_NEW_LIMIT + row["position"]
            legacy_matches &= row["base_date"] == old_day and row["sequence"] == old_sequence
            current_matches &= row["base_date"] == new_day and row["sequence"] == new_sequence
            new_values.append((new_day, new_sequence, row["question_id"]))
        if current_matches:
            return {"changed": False, "study_days": PLANNED_DAYS,
                    "finish": (START_DATE + timedelta(days=PLANNED_DAYS - 1)).isoformat()}
        if not legacy_matches:
            raise ValueError("目前排程與已知舊版不符，未更動資料")

        backup_dir = data_dir() / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"study-before-daily-schedule-{datetime.now(TZ):%Y%m%d-%H%M%S-%f}.sqlite3"
        backup = sqlite3.connect(backup_path)
        try:
            conn.backup(backup)
        finally:
            backup.close()

        review_updates = []
        for row in conn.execute("SELECT question_id,stage,due_date,updated_at FROM review_state WHERE mastered=0"):
            if not 0 <= row["stage"] < len(REVIEW_DELAYS):
                raise ValueError("錯題複習階段異常，未更動資料")
            changed_due = next_study_day(
                datetime.fromisoformat(row["updated_at"]).date(),
                REVIEW_DELAYS[row["stage"]],
            ).isoformat()
            if changed_due != row["due_date"]:
                review_updates.append((changed_due, row["question_id"]))

        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("UPDATE assignments SET sequence=sequence+10000")
            conn.executemany(
                "UPDATE assignments SET base_date=?,sequence=? WHERE question_id=?",
                new_values,
            )
            conn.executemany(
                "UPDATE review_state SET due_date=? WHERE question_id=?",
                review_updates,
            )
            conn.execute(
                "INSERT INTO settings(key,value) VALUES('schedule_version','daily-v2') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
            )
            if conn.execute("SELECT COUNT(DISTINCT base_date) FROM assignments").fetchone()[0] != PLANNED_DAYS:
                raise ValueError("新排程天數驗證失敗")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return {
            "changed": True,
            "backup": str(backup_path),
            "questions": len(rows),
            "review_due_updated": len(review_updates),
            "attempts_preserved": conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0],
            "finish": (START_DATE + timedelta(days=PLANNED_DAYS - 1)).isoformat(),
        }
    finally:
        conn.close()


if __name__ == "__main__":
    import json
    print(json.dumps(migrate(), ensure_ascii=False, indent=2))
