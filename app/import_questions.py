"""Import the verified Excel index and original exam PDFs into a local question bank."""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import logging
import re
import subprocess
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import openpyxl
import pdfplumber

from manual_overrides import IMAGE_PAGE_OVERRIDES, MANUAL_OVERRIDES
from study_core import ARCHIVE_DIR, WORKBOOK, connect, data_dir, init_db


logging.getLogger("pdfminer").setLevel(logging.ERROR)
QUESTION_MARKER = re.compile(r"(?m)^[ \t\u00a0]*(\d{1,2})\.[ \t]*")
CHOICE_MARKER = re.compile(r"(?<![A-Za-z0-9])([ABCD])[.．][ \t]*")
LINE_CHOICE_MARKER = re.compile(r"(?m)^[ \t]*([ABCD])[.．][ \t]*")


def clean_text(value: str) -> str:
    lines = [re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def flat_text(value: str) -> str:
    return re.sub(r"\s+", "", value).replace("（", "(").replace("）", ")")


def parse_options(body: str, summary: str) -> tuple[str, dict[str, str], list[str]]:
    line_markers = list(LINE_CHOICE_MARKER.finditer(body))
    markers = line_markers if all(any(m.group(1) == letter for m in line_markers) for letter in "ABCD") else list(CHOICE_MARKER.finditer(body))
    quartet = None
    for i, marker in enumerate(markers):
        if marker.group(1) != "A":
            continue
        picked = [marker]
        for letter in "BCD":
            next_marker = next((m for m in markers if m.start() > picked[-1].start() and m.group(1) == letter), None)
            if next_marker is None:
                break
            picked.append(next_marker)
        if len(picked) == 4:
            quartet = picked
            break
    if quartet is None:
        raise ValueError("A-D 選項無法完整辨識")
    stem = clean_text(body[:quartet[0].start()])
    options = {}
    for i, letter in enumerate("ABCD"):
        end = quartet[i + 1].start() if i < 3 else len(body)
        options[letter] = clean_text(body[quartet[i].end():end])
    warnings = []
    if not stem or any(not text for text in options.values()):
        warnings.append("題幹或選項為空")
    if len(stem) > 1200 or any(len(text) > 850 for text in options.values()):
        warnings.append("題幹或選項長度異常")
    similarity = SequenceMatcher(None, flat_text(summary)[:150], flat_text(stem)[:150]).ratio()
    if similarity < 0.55:
        warnings.append(f"索引摘要相似度低:{similarity:.2f}")
    if re.search(r"(?m)^\d{1,2}\.", stem):
        warnings.append("題幹包含其他題號")
    doubled = len(re.findall(r"([\u4e00-\u9fff])\1", stem + " ".join(options.values())))
    if doubled >= 3:
        warnings.append(f"PDF 文字重影:{doubled}")
    return stem, options, warnings


def parse_pdf(path: Path, rows: list[dict]) -> dict[int, tuple[str, dict[str, str], list[str], list[int]]]:
    with pdfplumber.open(path) as pdf:
        # Some official PDFs contain duplicate glyphs at the same coordinates.
        # They look normal when rendered, but plain text extraction repeats every
        # character (for example, ``APTT`` becomes ``AAPPTTTT``).  Remove only
        # coordinate-overlapping glyphs before extracting; real repeated letters
        # at different positions remain untouched.
        texts = [page.dedupe_chars().extract_text() or "" for page in pdf.pages]
    page_starts = []
    offset = 0
    for page_text in texts:
        page_starts.append(offset)
        offset += len(page_text) + 1
    full = "\n".join(texts)
    matches = []
    expected = 1
    for marker in QUESTION_MARKER.finditer(full):
        if int(marker.group(1)) == expected:
            matches.append(marker)
            expected += 1
            if expected == 81:
                break
    if len(matches) != 80:
        raise ValueError(f"{path.name}: 只辨識到 {len(matches)} / 80 個依序題號")
    by_number = {r["number"]: r for r in rows}
    result = {}
    for i, marker in enumerate(matches):
        number = i + 1
        end = matches[i + 1].start() if i < 79 else len(full)
        body = full[marker.end():end]
        row = by_number[number]
        stem, choices, warnings = parse_options(body, row["summary"])
        start_page = bisect.bisect_right(page_starts, marker.start())
        if start_page != row["source_page"]:
            warnings.append(f"索引頁碼差異:{start_page}")
        extra_pages = []
        for page in range(start_page + 1, len(texts) + 1):
            tail_start = max(marker.end(), page_starts[page - 1])
            tail_end = min(end, page_starts[page - 1] + len(texts[page - 1]))
            if tail_start < tail_end and clean_text(full[tail_start:tail_end]):
                extra_pages.append(page)
        result[number] = (stem, choices, warnings, extra_pages)
    return result


def read_index() -> tuple[list[dict], list[str]]:
    book = openpyxl.load_workbook(WORKBOOK, read_only=True, data_only=True)
    sheet = book["逐題索引"]
    rows = []
    for values in sheet.iter_rows(min_row=5, values_only=True):
        if not values or not values[0]:
            continue
        question_pdf = (WORKBOOK.parent / values[13]).resolve()
        answer_pdf = (WORKBOOK.parent / values[15]).resolve()
        if not question_pdf.is_file() or not answer_pdf.is_file():
            raise FileNotFoundError(values[0])
        rows.append({
            "id": values[0], "year": int(values[1]), "sitting": int(values[2]),
            "subject": values[3], "number": int(values[4]), "topic": values[5],
            "subtopic": values[6], "secondary_tag": values[7], "kind": values[8],
            "answer_raw": values[9], "answer_note": values[10], "summary": values[11],
            "needs_image": values[12] == "是", "source_pdf": str(question_pdf.relative_to(ARCHIVE_DIR)),
            "source_page": int(values[14]), "answer_pdf": str(answer_pdf.relative_to(ARCHIVE_DIR)),
        })
    subjects = list(dict.fromkeys(r["subject"] for r in rows))
    if len(rows) != 4800 or len({r["id"] for r in rows}) != 4800 or len(subjects) != 6:
        raise ValueError("逐題索引數量、ID 或科目不符")
    book.close()
    return rows, subjects


def image_name(source_pdf: str, page: int) -> str:
    digest = hashlib.sha256(f"{source_pdf}#{page}".encode()).hexdigest()[:20]
    return f"{digest}.jpg"


def render_image_pages(rows: list[dict], folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    pages = {(r["source_pdf"], r["source_page"]) for r in rows if r["image_file"]}
    pages.update((r["source_pdf"], page) for r in rows for page in r["extra_pages"] if r["image_file"])
    for source_pdf, page in sorted(pages):
        target = folder / image_name(source_pdf, page)
        if target.exists() and target.stat().st_size > 10000:
            continue
        command = ["pdftoppm", "-f", str(page), "-l", str(page), "-singlefile", "-jpeg", "-r", "150", "-jpegopt", "quality=86", str(ARCHIVE_DIR / source_pdf), str(target.with_suffix(""))]
        subprocess.run(command, check=True, capture_output=True)
        if not target.exists() or target.stat().st_size < 10000:
            raise ValueError(f"圖表頁轉檔失敗: {source_pdf} 第 {page} 頁")


def import_bank(target_dir: Path, render_images: bool = True) -> dict:
    rows, subjects = read_index()
    by_pdf = defaultdict(list)
    for row in rows:
        by_pdf[row["source_pdf"]].append(row)
    if len(by_pdf) != 60:
        raise ValueError("試卷數量不符")
    warnings = []
    for relative, group in sorted(by_pdf.items()):
        parsed = parse_pdf(ARCHIVE_DIR / relative, group)
        for row in group:
            stem, options, flags, extra_pages = parsed[row["number"]]
            override = MANUAL_OVERRIDES.get(row["id"])
            if override:
                stem = override.get("stem", stem)
                options = override["options"]
                flags = [override["warning"]]
            row["stem"] = stem
            row["options"] = options
            row["parse_warning"] = "; ".join(flags) if flags else None
            row["image_file"] = image_name(relative, row["source_page"]) if row["needs_image"] or flags else None
            row["extra_pages"] = extra_pages
            image_extra_pages = extra_pages if row["image_file"] else []
            image_extra_pages = sorted(set(image_extra_pages + IMAGE_PAGE_OVERRIDES.get(row["id"], [])))
            row["image_extra_json"] = json.dumps(
                [image_name(relative, page) for page in image_extra_pages]
            )
            if flags:
                warnings.append({"id": row["id"], "flags": flags, "source_pdf": relative, "page": row["source_page"]})
    target_dir.mkdir(parents=True, exist_ok=True)
    if render_images:
        render_image_pages(rows, target_dir / "images")
    path = target_dir / "study.sqlite3"
    conn = connect(path)
    init_db(conn)
    if conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]:
        conn.close()
        raise RuntimeError("題庫已存在；為保留作答歷史，匯入器不會覆寫")
    question_values = [(
        r["id"], r["year"], r["sitting"], r["subject"], r["number"],
        r["topic"], r["subtopic"], r["secondary_tag"], r["kind"],
        r["stem"], json.dumps(r["options"], ensure_ascii=False), r["answer_raw"],
        r["answer_note"], r["summary"], int(r["needs_image"]), r["image_file"], r["image_extra_json"],
        r["source_pdf"], r["source_page"], r["answer_pdf"], r["parse_warning"],
    ) for r in rows]
    with conn:
        conn.executemany(
            """INSERT INTO questions(id,year,sitting,subject,number,topic,subtopic,secondary_tag,kind,stem,options_json,answer_raw,answer_note,summary,needs_image,image_file,image_extra_json,source_pdf,source_page,answer_pdf,parse_warning)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", question_values,
        )
    checks = {
        "questions": conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0],
        "modules": conn.execute("SELECT COUNT(*) FROM (SELECT 1 FROM questions GROUP BY subject,topic,subtopic)").fetchone()[0],
        "subjects": dict(conn.execute("SELECT subject,COUNT(*) FROM questions GROUP BY subject")),
        "image_questions": conn.execute("SELECT COUNT(*) FROM questions WHERE needs_image=1").fetchone()[0],
        "corrected_questions": conn.execute("SELECT COUNT(*) FROM questions WHERE answer_note IS NOT NULL").fetchone()[0],
        "warnings": warnings,
    }
    conn.close()
    (target_dir / "import_qa.json").write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
    return checks


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=data_dir())
    parser.add_argument("--skip-images", action="store_true", help="Only for development checks")
    args = parser.parse_args()
    report = import_bank(args.data_dir.expanduser().resolve(), not args.skip_images)
    print(json.dumps({k: v if k != "warnings" else len(v) for k, v in report.items()}, ensure_ascii=False, indent=2))
