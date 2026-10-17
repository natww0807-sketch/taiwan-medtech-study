"""Shared storage, schedule, grading, and review logic for the local study app."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


APP_DIR = Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR)).resolve()
ARCHIVE_DIR = (RESOURCE_DIR / "content") if hasattr(sys, "_MEIPASS") else (APP_DIR.parent / "content")
WORKBOOK = ARCHIVE_DIR / "歷年考點分析" / "近五年考點分類索引.xlsx"
KNOWLEDGE_NOTE = ARCHIVE_DIR / "歷年考點分析" / "臨床生理學與病理學_知識點筆記_待審.md"
MICROBIOLOGY_KNOWLEDGE_NOTE = ARCHIVE_DIR / "歷年考點分析" / "微生物學與臨床微生物學_知識點筆記_待審.md"
HEMATOLOGY_KNOWLEDGE_NOTE = ARCHIVE_DIR / "歷年考點分析" / "臨床血液學與血庫學_知識點筆記_待審.md"
QUESTION_SUPPLEMENTS = RESOURCE_DIR / "question_supplements.json"
KNOWLEDGE_NOTE_SOURCES = (
    (KNOWLEDGE_NOTE, "臨床生理學與病理學"),
    (MICROBIOLOGY_KNOWLEDGE_NOTE, "微生物學與臨床微生物學（包括細菌與黴菌）"),
    (HEMATOLOGY_KNOWLEDGE_NOTE, "臨床血液學與血庫學"),
)
QUESTION_ID_PATTERN = r"11[1-5]-[12]-[^-\s`/]+-[0-9]{2}"
START_DATE = date(2026, 10, 13)
PLANNED_DAYS = 48
END_DATE = START_DATE + timedelta(days=PLANNED_DAYS - 1)
TZ = ZoneInfo("Asia/Taipei")
DAILY_NEW_LIMIT = 100
MODULE_BATCH_SIZE = 20
ACTIVE_MODULE_KEY = "active_module"
NOTE_MAX_LENGTH = 50_000


# Some formula glyphs are stored as positioned PDF objects and disappear from
# extracted text entirely. These source-verified replacements are applied only
# when presenting a card; the imported question rows remain unchanged.
_QUESTION_TEXT_CORRECTIONS: dict[str, dict[str, str]] = {
    "111-1-生理病理-20": {
        "B": "動脈及靜脈血的 PO₂ 分別約為83～108 mmHg及35～50 mmHg",
        "C": "動脈血之 PCO₂ 比靜脈血略為上升3～5 mmHg",
    },
    "111-1-生理病理-21": {
        "B": "PCO₂ 下降可能代表換氣過少，而二氧化碳滯留在肺中",
        "C": "HCO₃⁻可以跟 PCO₂ 一起影響體內的酸鹼值",
    },
    "112-1-生理病理-23": {
        "stem": "一位糖尿病患者腹痛數日，血液氣體分析結果為pH 7.26、PCO₂：28.6 mmHg、PO₂：90 mmHg、HCO₃⁻：11.0 mmol/L，下列診斷結果何者最合理？",
    },
    "112-2-生理病理-21": {
        "stem": "50歲長期抽菸男性，因胸悶到醫院檢查，手指檢測血氧飽和度監測（SO₂）為95%，血液氣體分析結果為pH 7.38、PCO₂ 50 mmHg、PO₂ 50 mmHg，初步分析何者錯誤？",
        "A": "SO₂為95%，但抽血 PO₂ 只有50 mmHg，可能是抽到靜脈血而非動脈血",
        "B": "PCO₂ 50 mmHg，可能是抽到靜脈血",
        "C": "PCO₂ 上升情況，可能是檢體未冰浴又放置太久",
    },
    "113-1-生理病理-23": {
        "stem": "一位急性病患的血液氣體分析結果如下：pH 7.19、PCO₂：20.7 mmHg、PO₂：88 mmHg、HCO₃⁻：8.8 mmol/L，判讀結果最可能是下列何者？",
    },
}


# PDF text extraction sometimes separates the first glyph from the rest of a
# question or option (for example, "心 衰竭" or "S taphylococcus"). Keep the
# database text untouched and repair only the text returned to the UI.
_CJK_GAP = re.compile(r"(?<=[\u3400-\u9fff])[ \t\u00a0]+(?=[\u3400-\u9fff])")
_LEADING_LATIN_GAP = re.compile(
    r"(?m)^([A-Za-z])[ \t\u00a0]+([A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ0-9'’.-]*)"
)
_PROTECTED_SINGLE_LETTER_TERMS = {
    "antigen", "band", "block", "cell", "cells", "chain", "collagen",
    "disease", "form", "gene", "group", "infection", "interval", "law",
    "lymphocyte", "mode", "phase", "protein", "receptor", "region",
    "segment", "site", "syndrome", "test", "type", "virus", "wave",
}

_DETACHED_FORMULA = re.compile(
    r"(?<![A-Za-z0-9])(?:cHCO|PaCO|PaO|SaO|SvO|pCO|pO|pN|PCO|PO|HCO|HPO|SO|CO|O|N|P)(?![A-Za-z0-9₀-₉])"
)
_SUBSCRIPT_TRANSLATION = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")


def _repair_detached_formula_subscripts(value: str) -> str:
    """Reattach PDF subscripts emitted as a separate following line.

    Official exam PDFs often extract a row such as ``pO ... pCO ...`` and put
    ``2 2`` on the next line.  Matching the formula markers and number tokens
    by position repairs the notation without changing legitimate ``pCO`` for
    carbon monoxide or ``PO`` for peroxidase.
    """
    lines = value.split("\n")
    output: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if index + 1 < len(lines) and re.fullmatch(r"\s*(?:\d+\s*)+", lines[index + 1]):
            matches = list(_DETACHED_FORMULA.finditer(line))
            subscripts = re.findall(r"\d+", lines[index + 1])
            if matches and len(matches) == len(subscripts):
                for match, number in reversed(list(zip(matches, subscripts))):
                    replacement = match.group(0) + number.translate(_SUBSCRIPT_TRANSLATION)
                    line = line[:match.start()] + replacement + line[match.end():]
                output.append(line)
                index += 2
                continue
        output.append(line)
        index += 1
    text = "\n".join(output)
    return re.sub(
        r"(?<![A-Za-z0-9])(PaCO|PaO|SaO|SvO|pCO|pO|pN|PCO|PO|SO|CO|O|N)2(?![A-Za-z0-9])",
        lambda match: match.group(1) + "₂",
        text,
    )


def clean_question_text(value: str) -> str:
    """Repair common PDF glyph-spacing artifacts for card display only."""
    text = _CJK_GAP.sub("", value)

    def join_leading_word(match: re.Match[str]) -> str:
        first, remainder = match.groups()
        if remainder.lower() in _PROTECTED_SINGLE_LETTER_TERMS:
            return match.group(0)
        return first + remainder

    text = _LEADING_LATIN_GAP.sub(join_leading_word, text)
    text = re.sub(r"(?<![A-Za-z0-9])F[ \t\u00a0]+(?=EV\d|VC(?![A-Za-z0-9]))", "F", text)
    text = re.sub(r"(?<![A-Za-z0-9])P[ \t\u00a0]+(?=EFR(?![A-Za-z0-9])|R(?![A-Za-z0-9]))", "P", text)
    text = re.sub(r"(?<![A-Za-z0-9])Q[ \t\u00a0]+(?=RS(?![A-Za-z0-9]))", "Q", text)
    text = re.sub(r"(?<![A-Za-z0-9])S[ \t\u00a0]+(?=T(?![A-Za-z0-9]))", "S", text)
    text = re.sub(r"(?<![A-Za-z0-9])a[ \t\u00a0]+(?=V[FLR](?![A-Za-z0-9]))", "a", text)
    text = re.sub(r"(?<![A-Za-z0-9])V[ \t\u00a0]+(?=[1-6](?![A-Za-z0-9]))", "V", text)
    text = re.sub(r"(\d)[ \t\u00a0]+(?=\d[\u3400-\u9fff])", r"\1", text)
    return _repair_detached_formula_subscripts(text)


def display_question_text(question_id: str, field: str, value: str) -> str:
    """Return cleaned card text, using source-verified missing-glyph repairs."""
    replacement = _QUESTION_TEXT_CORRECTIONS.get(question_id, {}).get(field, value)
    return clean_question_text(replacement)


def data_dir() -> Path:
    override = os.environ.get("MEDTECH_STUDY_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "醫技國考複習系統"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "醫技國考複習系統"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "醫技國考複習系統"


def db_path() -> Path:
    return data_dir() / "study.sqlite3"


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
  id TEXT PRIMARY KEY,
  year INTEGER NOT NULL,
  sitting INTEGER NOT NULL,
  subject TEXT NOT NULL,
  number INTEGER NOT NULL,
  topic TEXT NOT NULL,
  subtopic TEXT NOT NULL,
  secondary_tag TEXT,
  kind TEXT NOT NULL,
  stem TEXT NOT NULL,
  options_json TEXT NOT NULL,
  answer_raw TEXT NOT NULL,
  answer_note TEXT,
  summary TEXT NOT NULL,
  needs_image INTEGER NOT NULL,
  image_file TEXT,
  image_extra_json TEXT NOT NULL DEFAULT '[]',
  source_pdf TEXT NOT NULL,
  source_page INTEGER NOT NULL,
  answer_pdf TEXT NOT NULL,
  parse_warning TEXT,
  UNIQUE(year, sitting, subject, number)
);
CREATE TABLE IF NOT EXISTS assignments (
  question_id TEXT PRIMARY KEY REFERENCES questions(id),
  base_date TEXT NOT NULL,
  week_number INTEGER NOT NULL,
  position INTEGER NOT NULL,
  sequence INTEGER NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_assignments_date ON assignments(base_date, sequence);
CREATE INDEX IF NOT EXISTS idx_questions_category ON questions(subject, topic, subtopic);
CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id TEXT NOT NULL UNIQUE,
  question_id TEXT NOT NULL REFERENCES questions(id),
  selections_json TEXT NOT NULL,
  is_correct INTEGER NOT NULL,
  is_credit INTEGER NOT NULL,
  mode TEXT NOT NULL,
  answered_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_attempts_question ON attempts(question_id, answered_at);
CREATE INDEX IF NOT EXISTS idx_attempts_time ON attempts(answered_at);
CREATE TABLE IF NOT EXISTS review_state (
  question_id TEXT PRIMARY KEY REFERENCES questions(id),
  stage INTEGER NOT NULL,
  due_date TEXT,
  mastered INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_review_due ON review_state(due_date, mastered);
CREATE TABLE IF NOT EXISTS question_notes (
  question_id TEXT PRIMARY KEY REFERENCES questions(id),
  note TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS note_attachments (
  id TEXT PRIMARY KEY,
  question_id TEXT NOT NULL REFERENCES questions(id),
  original_name TEXT NOT NULL,
  storage_name TEXT NOT NULL UNIQUE,
  mime_type TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_note_attachments_question ON note_attachments(question_id, created_at);
CREATE TABLE IF NOT EXISTS knowledge_notes (
  subject TEXT NOT NULL,
  topic TEXT NOT NULL,
  subtopic TEXT NOT NULL,
  source_markdown TEXT NOT NULL,
  source_hash TEXT NOT NULL,
  custom_markdown TEXT,
  source_updated_at TEXT NOT NULL,
  user_updated_at TEXT,
  PRIMARY KEY(subject, topic, subtopic)
);
CREATE TABLE IF NOT EXISTS question_supplements (
  question_id TEXT PRIMARY KEY REFERENCES questions(id),
  background TEXT NOT NULL,
  steps TEXT NOT NULL,
  traps TEXT NOT NULL,
  extension TEXT NOT NULL,
  source_hash TEXT NOT NULL,
  source_updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO settings(key,value) VALUES('learning_mode','modules-v1') "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    )
    conn.commit()


def module_dict(row) -> dict[str, str]:
    return {"subject": row["subject"], "topic": row["topic"], "subtopic": row["subtopic"]}


def module_exists(conn: sqlite3.Connection, module: dict | None) -> bool:
    return bool(module and conn.execute(
        "SELECT 1 FROM questions WHERE subject=? AND topic=? AND subtopic=? LIMIT 1",
        (module.get("subject"), module.get("topic"), module.get("subtopic")),
    ).fetchone())


def set_active_module(conn: sqlite3.Connection, subject: str, topic: str, subtopic: str) -> dict[str, str]:
    module = {"subject": subject, "topic": topic, "subtopic": subtopic}
    if not module_exists(conn, module):
        raise ValueError("找不到知識模塊")
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (ACTIVE_MODULE_KEY, json.dumps(module, ensure_ascii=False, separators=(",", ":"))),
    )
    conn.commit()
    return module


def get_active_module(conn: sqlite3.Connection) -> dict[str, str]:
    saved = conn.execute("SELECT value FROM settings WHERE key=?", (ACTIVE_MODULE_KEY,)).fetchone()
    if saved:
        try:
            module = json.loads(saved[0])
            if module_exists(conn, module):
                return module
        except (TypeError, json.JSONDecodeError):
            pass
    recent = conn.execute(
        """SELECT q.subject,q.topic,q.subtopic FROM attempts a
           JOIN questions q ON q.id=a.question_id ORDER BY a.answered_at DESC,a.id DESC LIMIT 1"""
    ).fetchone()
    first = recent or conn.execute(
        "SELECT subject,topic,subtopic FROM questions ORDER BY rowid LIMIT 1"
    ).fetchone()
    if not first:
        raise ValueError("題庫尚未匯入")
    return set_active_module(conn, first["subject"], first["topic"], first["subtopic"])


def stable_question_rank(question_id: str) -> str:
    return hashlib.sha256(f"module-v1|{question_id}".encode("utf-8")).hexdigest()


def module_batch(conn: sqlite3.Connection, subject: str, topic: str, subtopic: str, limit: int = MODULE_BATCH_SIZE) -> list[str]:
    if limit < 1 or limit > MODULE_BATCH_SIZE:
        raise ValueError("每批最多 20 題")
    rows = conn.execute(
        """SELECT q.id FROM questions q
           WHERE q.subject=? AND q.topic=? AND q.subtopic=?
             AND NOT EXISTS(SELECT 1 FROM attempts a WHERE a.question_id=q.id)""",
        (subject, topic, subtopic),
    ).fetchall()
    return [row["id"] for row in sorted(rows, key=lambda row: (stable_question_rank(row["id"]), row["id"]))[:limit]]


def mark_mastered(conn: sqlite3.Connection, question_id: str, mastered: bool = True) -> dict:
    if not conn.execute("SELECT 1 FROM questions WHERE id=?", (question_id,)).fetchone():
        raise ValueError("找不到題目")
    state = conn.execute("SELECT 1 FROM review_state WHERE question_id=?", (question_id,)).fetchone()
    if mastered and not state:
        raise ValueError("本題目前不在未熟練錯題中")
    stamp = datetime.now(TZ).isoformat()
    with conn:
        conn.execute(
            """INSERT INTO review_state(question_id,stage,due_date,mastered,updated_at) VALUES(?,0,NULL,?,?)
               ON CONFLICT(question_id) DO UPDATE SET mastered=excluded.mastered,updated_at=excluded.updated_at""",
            (question_id, int(mastered), stamp),
        )
    return {"question_id": question_id, "mastered": mastered}


def knowledge_subject_for(path: Path) -> str:
    resolved = path.resolve()
    for source_path, subject in KNOWLEDGE_NOTE_SOURCES:
        if source_path.resolve() == resolved:
            return subject
    raise ValueError(f"尚未設定知識筆記對應科目：{path.name}")


def parse_knowledge_note(path: Path = KNOWLEDGE_NOTE, subject: str | None = None) -> list[dict[str, str]]:
    """Split the reviewed subject note into the workbook's topic/subtopic pairs."""
    subject = subject or knowledge_subject_for(path)
    text = path.read_text(encoding="utf-8")
    main = text.split("\n## 官方更正", 1)[0]
    topic = None
    sections: list[dict[str, str]] = []
    current: dict[str, str | list[str]] | None = None
    for line in main.splitlines():
        if line.startswith("## "):
            if current:
                markdown = "\n".join(current["lines"]).strip()
                current["markdown"] = re.sub(
                    rf"\[({QUESTION_ID_PATTERN})\]\(\.\./[^)#]+#page=([0-9]+)\)",
                    r"[\1](/api/source/\1#page=\2)", markdown,
                )
                current.pop("lines")
                sections.append(current)  # type: ignore[arg-type]
            topic = re.sub(r"^[一二三四五六七八九十]+、", "", line[3:]).strip()
            current = None
        elif line.startswith("### ") and topic:
            if current:
                markdown = "\n".join(current["lines"]).strip()
                current["markdown"] = re.sub(
                    rf"\[({QUESTION_ID_PATTERN})\]\(\.\./[^)#]+#page=([0-9]+)\)",
                    r"[\1](/api/source/\1#page=\2)", markdown,
                )
                current.pop("lines")
                sections.append(current)  # type: ignore[arg-type]
            current = {"subject": subject, "topic": topic, "subtopic": line[4:].strip(), "lines": []}
        elif current:
            current["lines"].append(line)
    if current:
        markdown = "\n".join(current["lines"]).strip()
        current["markdown"] = re.sub(
            rf"\[({QUESTION_ID_PATTERN})\]\(\.\./[^)#]+#page=([0-9]+)\)",
            r"[\1](/api/source/\1#page=\2)", markdown,
        )
        current.pop("lines")
        sections.append(current)  # type: ignore[arg-type]
    return sections


def sync_knowledge_notes(conn: sqlite3.Connection, path: Path | None = None) -> int:
    """Refresh imported source text while preserving every saved user revision."""
    sources = ((path, knowledge_subject_for(path)),) if path else KNOWLEDGE_NOTE_SOURCES
    imported = 0
    with conn:
        for source_path, subject in sources:
            if not source_path.is_file():
                continue
            stamp = datetime.fromtimestamp(source_path.stat().st_mtime, TZ).isoformat()
            sections = parse_knowledge_note(source_path, subject)
            for item in sections:
                markdown = item["markdown"]
                digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
                conn.execute(
                    """INSERT INTO knowledge_notes(subject,topic,subtopic,source_markdown,source_hash,source_updated_at)
                       VALUES(?,?,?,?,?,?)
                       ON CONFLICT(subject,topic,subtopic) DO UPDATE SET
                         source_markdown=excluded.source_markdown,
                         source_hash=excluded.source_hash,
                         source_updated_at=excluded.source_updated_at""",
                    (item["subject"], item["topic"], item["subtopic"], markdown, digest, stamp),
                )
            imported += len(sections)
    return imported


def knowledge_note(conn: sqlite3.Connection, subject: str, topic: str, subtopic: str) -> dict:
    row = conn.execute(
        """SELECT subject,topic,subtopic,source_markdown,custom_markdown,source_updated_at,user_updated_at
           FROM knowledge_notes WHERE subject=? AND topic=? AND subtopic=?""",
        (subject, topic, subtopic),
    ).fetchone()
    if row is None:
        raise ValueError("此分類尚未匯入知識筆記")
    item = dict(row)
    item["markdown"] = item["custom_markdown"] if item["custom_markdown"] is not None else item["source_markdown"]
    item["customized"] = item["custom_markdown"] is not None
    item.pop("source_markdown")
    item.pop("custom_markdown")
    return item


def save_knowledge_note(conn: sqlite3.Connection, subject: str, topic: str, subtopic: str, markdown: str) -> dict:
    if not isinstance(markdown, str) or len(markdown) > 200_000:
        raise ValueError("知識筆記內容過長")
    if not conn.execute(
        "SELECT 1 FROM knowledge_notes WHERE subject=? AND topic=? AND subtopic=?",
        (subject, topic, subtopic),
    ).fetchone():
        raise ValueError("此分類尚未匯入知識筆記")
    stamp = datetime.now(TZ).isoformat()
    with conn:
        conn.execute(
            """UPDATE knowledge_notes SET custom_markdown=?,user_updated_at=?
               WHERE subject=? AND topic=? AND subtopic=?""",
            (markdown, stamp, subject, topic, subtopic),
        )
    return knowledge_note(conn, subject, topic, subtopic)


def sync_question_supplements(conn: sqlite3.Connection, path: Path = QUESTION_SUPPLEMENTS) -> int:
    """Import the four approved post-answer sections for every question."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("format") != "medtech-question-supplements-v1":
        raise ValueError("逐題背景補充格式無效")
    categories = payload.get("categories")
    questions = payload.get("questions")
    if not isinstance(categories, dict) or not isinstance(questions, dict) or not questions:
        raise ValueError("逐題背景補充數量異常")
    valid_ids = {row[0] for row in conn.execute("SELECT id FROM questions")}
    if set(questions) != valid_ids:
        raise ValueError("逐題背景補充與題庫 ID 不一致")
    stamp = datetime.fromtimestamp(path.stat().st_mtime, TZ).isoformat()
    rows = []
    required = ("background", "steps", "traps", "extension")
    for question_id, category_id in questions.items():
        item = categories.get(category_id)
        if not isinstance(item, dict) or any(not isinstance(item.get(key), str) or not item[key].strip() for key in required):
            raise ValueError(f"逐題背景補充內容不完整：{question_id}")
        digest = hashlib.sha256(
            json.dumps({key: item[key] for key in required}, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        rows.append((question_id, *(item[key] for key in required), digest, stamp))
    with conn:
        conn.executemany(
            """INSERT INTO question_supplements(question_id,background,steps,traps,extension,source_hash,source_updated_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(question_id) DO UPDATE SET
                 background=excluded.background,
                 steps=excluded.steps,
                 traps=excluded.traps,
                 extension=excluded.extension,
                 source_hash=excluded.source_hash,
                 source_updated_at=excluded.source_updated_at""",
            rows,
        )
    return len(rows)


def question_supplement(conn: sqlite3.Connection, question_id: str) -> dict | None:
    row = conn.execute(
        "SELECT background,steps,traps,extension FROM question_supplements WHERE question_id=?",
        (question_id,),
    ).fetchone()
    return dict(row) if row else None


def today() -> date:
    return datetime.now(TZ).date()


def is_study_day(day: date) -> bool:
    return day >= START_DATE


def next_study_day(day: date, steps: int = 1) -> date:
    if steps < 1:
        raise ValueError("steps must be positive")
    while steps:
        day += timedelta(days=1)
        if is_study_day(day):
            steps -= 1
    return day


def planned_dates() -> list[date]:
    return [START_DATE + timedelta(days=n) for n in range(PLANNED_DAYS)]


def accepted_answers(raw: str) -> set[str] | None:
    if raw in {"全給分", "作答即給分"}:
        return None
    return {"".join(sorted(part.strip().upper())) for part in raw.split("/") if part.strip()}


def allows_multiple(raw: str) -> bool:
    answers = accepted_answers(raw)
    return bool(answers and any(len(value) > 1 for value in answers))


def grade(raw: str, selections: list[str]) -> tuple[bool, bool]:
    if not selections or len(set(selections)) != len(selections) or any(x not in "ABCD" or len(x) != 1 for x in selections):
        raise ValueError("請選擇 A、B、C 或 D")
    accepted = accepted_answers(raw)
    if accepted is None:
        return True, True
    if len(selections) > 1 and not allows_multiple(raw):
        raise ValueError("本題只能選一個答案")
    return "".join(sorted(selections)) in accepted, False


def question_public(row: sqlite3.Row, reveal: bool = False) -> dict:
    options = {
        letter: display_question_text(row["id"], letter, text)
        for letter, text in json.loads(row["options_json"]).items()
    }
    item = {
        "id": row["id"], "year": row["year"], "sitting": row["sitting"],
        "subject": row["subject"], "number": row["number"],
        "topic": row["topic"], "subtopic": row["subtopic"],
        "secondary_tag": row["secondary_tag"], "kind": row["kind"],
        "stem": display_question_text(row["id"], "stem", row["stem"]), "options": options,
        "summary": row["summary"], "needs_image": bool(row["needs_image"]),
        "has_image": bool(row["image_file"]), "source_page": row["source_page"],
        "allows_multiple": allows_multiple(row["answer_raw"]),
        "has_correction": bool(row["answer_note"]),
        "parse_warning": row["parse_warning"],
    }
    if reveal:
        item.update(answer=row["answer_raw"], answer_note=row["answer_note"])
    return item


def first_attempt_count_on(conn: sqlite3.Connection, day: date) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM attempts WHERE mode='new' AND substr(answered_at,1,10)=?",
        (day.isoformat(),),
    ).fetchone()[0]


def daily_new_ids(conn: sqlite3.Connection, day: date) -> list[str]:
    if day < START_DATE or not is_study_day(day):
        return []
    capacity = max(0, DAILY_NEW_LIMIT - first_attempt_count_on(conn, day))
    return [r[0] for r in conn.execute(
        """SELECT a.question_id FROM assignments a
           WHERE a.base_date<=? AND NOT EXISTS
             (SELECT 1 FROM attempts t WHERE t.question_id=a.question_id)
           ORDER BY a.sequence LIMIT ?""",
        (day.isoformat(), capacity),
    )]


def due_review_ids(conn: sqlite3.Connection, day: date, limit: int | None = None) -> list[str]:
    if not is_study_day(day):
        return []
    sql = "SELECT question_id FROM review_state WHERE mastered=0 AND due_date<=? ORDER BY due_date, updated_at, question_id"
    params: tuple = (day.isoformat(),)
    if limit is not None:
        sql += " LIMIT ?"
        params += (limit,)
    return [r[0] for r in conn.execute(sql, params)]


def projected_finish(conn: sqlite3.Connection, day: date) -> str:
    remaining = conn.execute("SELECT COUNT(*) FROM questions q WHERE NOT EXISTS (SELECT 1 FROM attempts a WHERE a.question_id=q.id)").fetchone()[0]
    if remaining == 0:
        completed_on = conn.execute(
            "SELECT MAX(substr(first_at,1,10)) FROM "
            "(SELECT MIN(answered_at) first_at FROM attempts GROUP BY question_id)"
        ).fetchone()[0]
        return completed_on or day.isoformat()
    cursor = max(day, START_DATE)
    if not is_study_day(cursor):
        cursor = next_study_day(cursor)
    first_capacity = max(0, DAILY_NEW_LIMIT - first_attempt_count_on(conn, cursor))
    remaining -= first_capacity
    while remaining > 0:
        cursor = next_study_day(cursor)
        remaining -= DAILY_NEW_LIMIT
    return cursor.isoformat()


def create_daily_backup(conn: sqlite3.Connection, day: date) -> Path:
    folder = data_dir() / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"study-{day.isoformat()}.sqlite3"
    if not target.exists():
        backup = sqlite3.connect(target)
        try:
            conn.backup(backup)
        finally:
            backup.close()
    return target


def record_attempt(conn: sqlite3.Connection, question_id: str, selections: list[str], mode: str, note: str, client_id: str, answered_at: datetime | None = None) -> dict:
    if not client_id or len(client_id) > 100:
        raise ValueError("作答識別碼無效")
    if mode not in {"new", "review", "practice", "module"}:
        raise ValueError("作答模式無效")
    if not isinstance(note, str) or len(note) > NOTE_MAX_LENGTH:
        raise ValueError("筆記內容過長")
    with conn:
        # Serialize writes so duplicate client ids stay idempotent.
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT id, question_id, is_correct, is_credit FROM attempts WHERE client_id=?", (client_id,)).fetchone()
        if existing:
            if existing["question_id"] != question_id:
                raise ValueError("重複的作答識別碼")
            question = conn.execute("SELECT * FROM questions WHERE id=?", (question_id,)).fetchone()
            active_wrong = bool(conn.execute("SELECT 1 FROM review_state WHERE question_id=? AND mastered=0", (question_id,)).fetchone())
            return {"attempt_id": existing["id"], "correct": bool(existing["is_correct"]), "credit": bool(existing["is_credit"]), "question": question_public(question, True), "supplement": question_supplement(conn, question_id), "active_wrong": active_wrong, "duplicate": True}
        question = conn.execute("SELECT * FROM questions WHERE id=?", (question_id,)).fetchone()
        if question is None:
            raise ValueError("找不到題目")
        is_correct, is_credit = grade(question["answer_raw"], selections)
        stamp = (answered_at or datetime.now(TZ)).astimezone(TZ)
        prior = conn.execute("SELECT COUNT(*) FROM attempts WHERE question_id=?", (question_id,)).fetchone()[0]
        actual_mode = mode
        if prior and actual_mode in {"new", "module"}:
            actual_mode = "review"
        cur = conn.execute(
            "INSERT INTO attempts(client_id,question_id,selections_json,is_correct,is_credit,mode,answered_at,note) VALUES(?,?,?,?,?,?,?,?)",
            (client_id, question_id, json.dumps(sorted(selections), ensure_ascii=False), int(is_correct), int(is_credit), actual_mode, stamp.isoformat(), note),
        )
        # A later answer replaces the question's current note. Avoid creating
        # empty rows for untouched questions, while still allowing a user to
        # deliberately clear a note that already exists.
        existing_note = conn.execute(
            "SELECT 1 FROM question_notes WHERE question_id=?", (question_id,)
        ).fetchone()
        if note.strip() or existing_note:
            conn.execute(
                "INSERT INTO question_notes(question_id,note,updated_at) VALUES(?,?,?) ON CONFLICT(question_id) DO UPDATE SET note=excluded.note,updated_at=excluded.updated_at",
                (question_id, note, stamp.isoformat()),
            )
        if not is_correct:
            conn.execute(
                "INSERT INTO review_state(question_id,stage,due_date,mastered,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(question_id) DO UPDATE SET stage=excluded.stage,due_date=excluded.due_date,mastered=excluded.mastered,updated_at=excluded.updated_at",
                (question_id, 0, None, 0, stamp.isoformat()),
            )
    create_daily_backup(conn, stamp.date())
    active_wrong = bool(conn.execute("SELECT 1 FROM review_state WHERE question_id=? AND mastered=0", (question_id,)).fetchone())
    return {"attempt_id": cur.lastrowid, "correct": is_correct, "credit": is_credit, "question": question_public(question, True), "supplement": question_supplement(conn, question_id), "active_wrong": active_wrong, "duplicate": False}


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 260000)
    return salt.hex() + ":" + digest.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, expected = stored.split(":", 1)
        actual = password_hash(password, bytes.fromhex(salt_hex)).split(":", 1)[1]
        return secrets.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False
