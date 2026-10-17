from __future__ import annotations

import json
import base64
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from study_core import (
    clean_question_text, connect, get_active_module, grade, init_db, knowledge_note, mark_mastered,
    module_batch, parse_knowledge_note, record_attempt, save_knowledge_note, set_active_module,
    sync_knowledge_notes,
    question_public, question_supplement, sync_question_supplements,
    HEMATOLOGY_KNOWLEDGE_NOTE, MICROBIOLOGY_KNOWLEDGE_NOTE,
)
from server import (
    delete_note_image, export_full_backup, export_progress, note_attachments,
    note_summaries, restore_full_backup, restore_progress, save_note, save_note_image,
)


class StudyLogicTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data_dir = os.environ.get("MEDTECH_STUDY_DATA_DIR")
        os.environ["MEDTECH_STUDY_DATA_DIR"] = self.temp.name
        self.conn = connect(Path(self.temp.name) / "study.sqlite3")
        init_db(self.conn)
        with self.conn:
            self.conn.execute(
                """INSERT INTO questions(id,year,sitting,subject,number,topic,subtopic,kind,stem,options_json,answer_raw,summary,needs_image,source_pdf,source_page,answer_pdf)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("q1", 115, 2, "測試科", 1, "主題", "子題", "基礎知識", "測試題幹", json.dumps({"A":"甲","B":"乙","C":"丙","D":"丁"}), "A", "測試題幹", 0, "test.pdf", 1, "answer.pdf"),
            )
            self.conn.execute("INSERT INTO assignments(question_id,base_date,week_number,position,sequence) VALUES('q1','2026-10-13',1,1,1)")

    def tearDown(self):
        self.conn.close()
        if self.old_data_dir is None:
            os.environ.pop("MEDTECH_STUDY_DATA_DIR", None)
        else:
            os.environ["MEDTECH_STUDY_DATA_DIR"] = self.old_data_dir
        self.temp.cleanup()

    def stamp(self, day):
        return datetime.fromisoformat(day + "T09:00:00").replace(tzinfo=ZoneInfo("Asia/Taipei"))

    def test_official_corrected_answers(self):
        self.assertEqual(grade("A/C/AC", ["C"]), (True, False))
        self.assertEqual(grade("A/C/AC", ["A", "C"]), (True, False))
        self.assertEqual(grade("A/C/AC", ["B"]), (False, False))
        self.assertEqual(grade("全給分", ["D"]), (True, True))
        with self.assertRaises(ValueError):
            grade("A", ["A", "B"])

    def test_question_text_repairs_pdf_glyph_spacing_for_display(self):
        self.assertEqual(clean_question_text("心 衰竭病患，平時長 期使用利尿劑"), "心衰竭病患，平時長期使用利尿劑")
        self.assertEqual(clean_question_text("下 列何者最不可能？"), "下列何者最不可能？")
        self.assertEqual(clean_question_text("S taphylococcus aureus"), "Staphylococcus aureus")
        self.assertEqual(clean_question_text("F EV1、F VC與P EFR"), "FEV1、FVC與PEFR")
        self.assertEqual(clean_question_text("V 4與a VF導程的S T變化"), "V4與aVF導程的ST變化")
        self.assertEqual(clean_question_text("4 5歲病患"), "45歲病患")

    def test_question_text_preserves_legitimate_scientific_spacing(self):
        self.assertEqual(clean_question_text("M mode超音波與3 MHz探頭"), "M mode超音波與3 MHz探頭")
        self.assertEqual(clean_question_text("T cell receptor"), "T cell receptor")
        self.assertEqual(clean_question_text("P antigen"), "P antigen")

    def test_question_text_restores_detached_blood_gas_subscripts(self):
        self.assertEqual(clean_question_text("氧氣分壓（pO ）\n2"), "氧氣分壓（pO₂ ）")
        self.assertEqual(
            clean_question_text("pH 上升、pO 下降、pCO 下降\n2 2"),
            "pH 上升、pO₂ 下降、pCO₂ 下降",
        )
        self.assertEqual(
            clean_question_text("PCO 上升很多，HCO- 略為代償上升\n2 3"),
            "PCO₂ 上升很多，HCO₃- 略為代償上升",
        )
        self.assertEqual(
            clean_question_text("P 的定義是氧飽和度50%時的pO，P 分別為多少？\n50 2 50"),
            "P₅₀ 的定義是氧飽和度50%時的pO₂，P₅₀ 分別為多少？",
        )
        self.assertEqual(clean_question_text("一氧化碳分壓（pCO）"), "一氧化碳分壓（pCO）")
        self.assertEqual(clean_question_text("peroxidase（PO）特殊染色"), "peroxidase（PO）特殊染色")
        self.assertEqual(clean_question_text("血氮分壓（pN）\n2"), "血氮分壓（pN₂）")
        self.assertEqual(
            clean_question_text("血液之PaO、［HCO-］、PaCO\n2 3 2"),
            "血液之PaO₂、［HCO₃-］、PaCO₂",
        )

    def test_question_public_restores_completely_missing_blood_gas_formulas(self):
        row = self.conn.execute(
            "SELECT * FROM questions WHERE id='111-1-生理病理-20'"
        ).fetchone()
        # The small fixture may not include official IDs, so verify the helper
        # directly when that row is absent.
        if row is None:
            from study_core import display_question_text
            self.assertEqual(
                display_question_text("111-1-生理病理-20", "B", "動脈及靜脈血的 分別約為"),
                "動脈及靜脈血的 PO₂ 分別約為83～108 mmHg及35～50 mmHg",
            )
            self.assertEqual(
                display_question_text("111-1-生理病理-20", "C", "動脈血之 比靜脈血"),
                "動脈血之 PCO₂ 比靜脈血略為上升3～5 mmHg",
            )
        else:
            public = question_public(row)
            self.assertIn("PO₂", public["options"]["B"])
            self.assertIn("PCO₂", public["options"]["C"])

    def test_wrong_stays_active_until_manually_mastered_and_reactivates(self):
        first = record_attempt(self.conn, "q1", ["B"], "module", "概念混淆", "one", self.stamp("2026-10-13"))
        self.assertFalse(first["correct"])
        self.assertTrue(first["active_wrong"])
        second = record_attempt(self.conn, "q1", ["A"], "review", "已釐清", "two", self.stamp("2026-10-14"))
        self.assertTrue(second["correct"])
        self.assertTrue(second["active_wrong"])
        self.assertIsNone(self.conn.execute("SELECT due_date FROM review_state WHERE question_id='q1'").fetchone()[0])
        mark_mastered(self.conn, "q1")
        self.assertEqual(self.conn.execute("SELECT mastered FROM review_state WHERE question_id='q1'").fetchone()[0], 1)
        third = record_attempt(self.conn, "q1", ["B"], "review", "又錯", "three", self.stamp("2026-10-15"))
        self.assertTrue(third["active_wrong"])
        self.assertEqual(self.conn.execute("SELECT mastered FROM review_state WHERE question_id='q1'").fetchone()[0], 0)
        self.assertTrue(record_attempt(self.conn, "q1", ["B"], "review", "", "three", self.stamp("2026-10-15"))["duplicate"])

    def test_note_added_after_grading_stays_with_that_attempt(self):
        first = record_attempt(self.conn, "q1", ["B"], "new", "", "first", self.stamp("2026-10-13"))
        save_note(self.conn, "q1", "我把兩種檢驗原理混淆了", first["attempt_id"])
        self.assertEqual(self.conn.execute("SELECT note FROM attempts WHERE id=?", (first["attempt_id"],)).fetchone()[0], "我把兩種檢驗原理混淆了")
        self.assertEqual(self.conn.execute("SELECT note FROM question_notes WHERE question_id='q1'").fetchone()[0], "我把兩種檢驗原理混淆了")
        second = record_attempt(self.conn, "q1", ["A"], "review", "已釐清", "second", self.stamp("2026-10-14"))
        save_note(self.conn, "q1", "查表後理解", second["attempt_id"])
        self.assertEqual([row[0] for row in self.conn.execute("SELECT note FROM attempts ORDER BY id")], ["我把兩種檢驗原理混淆了", "查表後理解"])
        self.assertEqual(export_progress(self.conn)["attempts"][-1]["note"], "查表後理解")
        with self.assertRaisesRegex(ValueError, "找不到這次作答"):
            save_note(self.conn, "q1", "不應保存", 9999)

    def test_note_summary_keeps_one_current_note_with_latest_attempt(self):
        record_attempt(self.conn, "q1", ["B"], "new", "第一次筆記", "note-one", self.stamp("2026-10-13"))
        rows = note_summaries(self.conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["note"], "第一次筆記")
        self.assertEqual(rows[0]["selections"], ["B"])
        record_attempt(self.conn, "q1", ["A"], "review", "更新後筆記", "note-two", self.stamp("2026-10-14"))
        rows = note_summaries(self.conn)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["note"], "更新後筆記")
        self.assertEqual(rows[0]["selections"], ["A"])
        self.assertTrue(rows[0]["is_correct"])
        self.assertTrue(rows[0]["answered_at"].startswith("2026-10-14"))
        save_note(self.conn, "q1", "總結頁修改")
        self.assertEqual(note_summaries(self.conn)[0]["note"], "總結頁修改")
        record_attempt(self.conn, "q1", ["A"], "review", "", "note-three", self.stamp("2026-10-15"))
        self.assertEqual(note_summaries(self.conn), [])

    def test_markdown_note_images_are_local_and_in_full_backup(self):
        with self.conn:
            self.conn.executemany(
                """INSERT INTO questions(id,year,sitting,subject,number,topic,subtopic,kind,stem,options_json,answer_raw,summary,needs_image,source_pdf,source_page,answer_pdf)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(f"q{i}",115,2,f"測試科{i}",1,"主題","子題","基礎知識","題幹",json.dumps({"A":"甲","B":"乙","C":"丙","D":"丁"}),"A","題幹",0,"test.pdf",1,"answer.pdf") for i in range(2,4801)],
            )
        png = b"\x89PNG\r\n\x1a\n" + b"local-note-image"
        item = save_note_image(
            self.conn, "q1", "血球表格.png", "image/png",
            base64.b64encode(png).decode("ascii"),
        )
        self.assertEqual(item["mime_type"], "image/png")
        self.assertIn(item["id"], item["markdown"])
        markdown = f"| 指標 | 結果 |\n|---|---|\n| MCV | 72 fL |\n\n{item['markdown']}"
        save_note(self.conn, "q1", markdown)
        archive = export_full_backup(self.conn)
        delete_note_image(self.conn, item["id"])
        self.assertEqual(note_attachments(self.conn, "q1"), [])
        with self.conn:
            self.conn.execute("DELETE FROM question_notes")
        result = restore_full_backup(self.conn, archive)
        self.assertEqual(result["attachments"], 1)
        restored = note_attachments(self.conn, "q1")
        self.assertEqual(restored[0]["name"], "血球表格.png")
        self.assertEqual(
            (Path(self.temp.name) / "note_images" / f"{item['id']}.png").read_bytes(), png
        )
        self.assertEqual(
            self.conn.execute("SELECT note FROM question_notes WHERE question_id='q1'").fetchone()[0], markdown
        )

    def test_note_image_validation_and_limits(self):
        with self.assertRaisesRegex(ValueError, "只接受"):
            save_note_image(self.conn, "q1", "x.gif", "image/gif", base64.b64encode(b"GIF89a").decode())
        with self.assertRaisesRegex(ValueError, "內容與檔案類型"):
            save_note_image(self.conn, "q1", "x.png", "image/png", base64.b64encode(b"not-png").decode())

    def test_question_supplement_is_revealed_only_after_attempt(self):
        self.assertNotIn("supplement", __import__("study_core").question_public(
            self.conn.execute("SELECT * FROM questions WHERE id='q1'").fetchone()
        ))
        source = Path(self.temp.name) / "question_supplements.json"
        source.write_text(json.dumps({
            "format": "medtech-question-supplements-v1",
            "categories": {
                "category-001": {
                    "background": "背景內容", "steps": "判讀內容",
                    "traps": "陷阱內容", "extension": "延伸內容",
                }
            },
            "questions": {"q1": "category-001"},
        }, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(sync_question_supplements(self.conn, source), 1)
        self.assertEqual(question_supplement(self.conn, "q1")["background"], "背景內容")
        result = record_attempt(self.conn, "q1", ["A"], "new", "", "supplement", self.stamp("2026-10-13"))
        self.assertEqual(result["supplement"], {
            "background": "背景內容", "steps": "判讀內容",
            "traps": "陷阱內容", "extension": "延伸內容",
        })
        duplicate = record_attempt(self.conn, "q1", ["A"], "new", "", "supplement", self.stamp("2026-10-13"))
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["supplement"]["extension"], "延伸內容")

    def test_module_batch_is_fixed_limited_and_resumes_unseen(self):
        with self.conn:
            self.conn.executemany(
                """INSERT INTO questions(id,year,sitting,subject,number,topic,subtopic,kind,stem,options_json,answer_raw,summary,needs_image,source_pdf,source_page,answer_pdf)
                   SELECT ?,year,sitting,subject,?,topic,subtopic,kind,stem,options_json,answer_raw,summary,needs_image,source_pdf,source_page,answer_pdf FROM questions WHERE id='q1'""",
                [(f"q{i}", i) for i in range(2, 27)],
            )
        first = module_batch(self.conn, "測試科", "主題", "子題")
        self.assertEqual(len(first), 20)
        self.assertEqual(first, module_batch(self.conn, "測試科", "主題", "子題"))
        self.assertEqual(len(first), len(set(first)))
        for index, question_id in enumerate(first[:3]):
            record_attempt(self.conn, question_id, ["A"], "module", "", f"batch-{index}", self.stamp("2026-10-12"))
        resumed = module_batch(self.conn, "測試科", "主題", "子題")
        self.assertTrue(set(first[:3]).isdisjoint(resumed))
        self.assertEqual(len(resumed), 20)

    def test_active_module_is_saved_and_uses_recent_attempt_on_first_migration(self):
        self.conn.execute("DELETE FROM settings WHERE key='active_module'")
        active = get_active_module(self.conn)
        self.assertEqual(active["subtopic"], "子題")
        self.assertEqual(get_active_module(self.conn), active)
        self.assertEqual(set_active_module(self.conn, "測試科", "主題", "子題"), active)

    def test_module_and_practice_have_no_date_or_daily_limit(self):
        early = record_attempt(self.conn, "q1", ["A"], "module", "", "early-module", self.stamp("2026-10-12"))
        self.assertTrue(early["correct"])
        self.assertEqual(self.conn.execute("SELECT mode FROM attempts WHERE client_id='early-module'").fetchone()[0], "module")

    def test_progress_export_and_restore(self):
        with self.conn:
            self.conn.executemany(
                """INSERT INTO questions(id,year,sitting,subject,number,topic,subtopic,kind,stem,options_json,answer_raw,summary,needs_image,source_pdf,source_page,answer_pdf)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(f"q{i}",115,2,f"測試科{i}",1,"主題","子題","基礎知識","題幹",json.dumps({"A":"甲","B":"乙","C":"丙","D":"丁"}),"A","題幹",0,"test.pdf",1,"answer.pdf") for i in range(2,4801)],
            )
        record_attempt(self.conn, "q1", ["B"], "new", "需要複習", "first", self.stamp("2026-10-13"))
        self.assertEqual(sync_knowledge_notes(self.conn), 85)
        save_knowledge_note(self.conn, "臨床生理學與病理學", "心臟", "心電圖與心律", "我的心電圖筆記")
        backup = export_progress(self.conn)
        self.assertEqual(backup["format"], "medtech-study-progress-v2")
        self.assertEqual(len(backup["attempts"]), 1)
        self.assertEqual(len(backup["knowledge_notes"]), 1)
        with self.conn:
            self.conn.execute("DELETE FROM attempts")
            self.conn.execute("DELETE FROM review_state")
            self.conn.execute("UPDATE knowledge_notes SET custom_markdown='稍後覆蓋'")
        result = restore_progress(self.conn, backup)
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 1)
        self.assertIsNone(self.conn.execute("SELECT due_date FROM review_state WHERE question_id='q1'").fetchone()[0])
        self.assertEqual(knowledge_note(self.conn, "臨床生理學與病理學", "心臟", "心電圖與心律")["markdown"], "我的心電圖筆記")
        self.assertTrue(Path(result["pre_restore_backup"]).is_file())
        legacy = json.loads(json.dumps(backup))
        legacy["format"] = "medtech-study-progress-v1"
        legacy.pop("preferences")
        with self.conn:
            self.conn.execute("DELETE FROM attempts")
            self.conn.execute("DELETE FROM review_state")
        legacy_result = restore_progress(self.conn, legacy)
        self.assertEqual(legacy_result["attempts"], 1)
        self.assertEqual(get_active_module(self.conn)["subtopic"], "子題")

    def test_knowledge_note_import_preserves_saved_revision(self):
        sections = parse_knowledge_note()
        self.assertEqual(len(sections), 31)
        self.assertEqual(len({(item["topic"], item["subtopic"]) for item in sections}), 31)
        self.assertTrue(any("/api/source/111-1-生理病理-" in item["markdown"] for item in sections))
        microbiology = parse_knowledge_note(MICROBIOLOGY_KNOWLEDGE_NOTE)
        self.assertEqual(len(microbiology), 23)
        self.assertEqual(len({(item["topic"], item["subtopic"]) for item in microbiology}), 23)
        self.assertTrue(all(item["subject"].startswith("微生物學") for item in microbiology))
        self.assertTrue(any("111-1-微生物-05" in item["markdown"] for item in microbiology))
        self.assertEqual(sync_knowledge_notes(self.conn), 85)
        saved = save_knowledge_note(self.conn, "臨床生理學與病理學", "心臟", "心電圖與心律", "自行補充內容")
        self.assertTrue(saved["customized"])
        self.assertEqual(sync_knowledge_notes(self.conn), 85)
        self.assertEqual(knowledge_note(self.conn, "臨床生理學與病理學", "心臟", "心電圖與心律")["markdown"], "自行補充內容")

    def test_microbiology_note_can_be_customized_without_changing_source(self):
        self.assertEqual(sync_knowledge_notes(self.conn), 85)
        subject = "微生物學與臨床微生物學（包括細菌與黴菌）"
        original = knowledge_note(self.conn, subject, "真菌", "酵母菌")["markdown"]
        self.assertIn("Candida", original)
        saved = save_knowledge_note(self.conn, subject, "真菌", "酵母菌", "我的酵母菌補充")
        self.assertEqual(saved["markdown"], "我的酵母菌補充")
        self.assertEqual(sync_knowledge_notes(self.conn), 85)
        self.assertEqual(knowledge_note(self.conn, subject, "真菌", "酵母菌")["markdown"], "我的酵母菌補充")

    def test_hematology_note_has_all_categories_and_preserves_custom_revision(self):
        sections = parse_knowledge_note(HEMATOLOGY_KNOWLEDGE_NOTE)
        self.assertEqual(len(sections), 31)
        self.assertEqual(len({(item["topic"], item["subtopic"]) for item in sections}), 31)
        self.assertTrue(all(item["subject"] == "臨床血液學與血庫學" for item in sections))
        self.assertTrue(any("115-2-血液血庫-" in item["markdown"] for item in sections))
        self.assertEqual(sync_knowledge_notes(self.conn), 85)
        saved = save_knowledge_note(
            self.conn, "臨床血液學與血庫學", "止血凝血", "凝血因子與篩檢", "我的凝血筆記"
        )
        self.assertEqual(saved["markdown"], "我的凝血筆記")
        self.assertEqual(sync_knowledge_notes(self.conn), 85)
        self.assertEqual(
            knowledge_note(self.conn, "臨床血液學與血庫學", "止血凝血", "凝血因子與篩檢")["markdown"],
            "我的凝血筆記",
        )


if __name__ == "__main__":
    unittest.main()
