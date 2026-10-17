"""Apply verified PDF transcriptions to an existing bank without touching progress."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from manual_overrides import MANUAL_OVERRIDES
from study_core import TZ, connect, data_dir


def main() -> None:
    with connect() as conn:
        existing = {row[0] for row in conn.execute("SELECT id FROM questions WHERE id IN (" + ",".join("?" for _ in MANUAL_OVERRIDES) + ")", tuple(MANUAL_OVERRIDES))}
        if existing != set(MANUAL_OVERRIDES):
            raise ValueError(f"題庫缺少人工校對題卡：{set(MANUAL_OVERRIDES) - existing}")
        backup_dir = data_dir() / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"pre-manual-qa-{datetime.now(TZ).strftime('%Y%m%d-%H%M%S-%f')}.sqlite3"
        with sqlite3.connect(backup_path) as backup:
            conn.backup(backup)
        with conn:
            for question_id, override in MANUAL_OVERRIDES.items():
                if "stem" in override:
                    conn.execute(
                        "UPDATE questions SET stem=?,options_json=?,parse_warning=? WHERE id=?",
                        (override["stem"], json.dumps(override["options"], ensure_ascii=False), override["warning"], question_id),
                    )
                else:
                    conn.execute(
                        "UPDATE questions SET options_json=?,parse_warning=? WHERE id=?",
                        (json.dumps(override["options"], ensure_ascii=False), override["warning"], question_id),
                    )
    print(f"已校對 {len(MANUAL_OVERRIDES)} 題；更新前備份：{backup_path}")


if __name__ == "__main__":
    main()
