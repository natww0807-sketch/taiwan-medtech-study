from __future__ import annotations

import http.client
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from study_core import password_hash


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("MEDTECH_STUDY_DATA_DIR")
        os.environ["MEDTECH_STUDY_DATA_DIR"] = self.temp.name
        server.prepare_database()

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("MEDTECH_STUDY_DATA_DIR", None)
        else:
            os.environ["MEDTECH_STUDY_DATA_DIR"] = self.previous
        self.temp.cleanup()

    def request(self, method, path, payload=None, cookie=None):
        headers = {}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
        if cookie:
            headers["Cookie"] = cookie
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read())
        result = response.status, data, response.getheader("Set-Cookie")
        connection.close()
        return result

    def test_clean_seed_counts_and_first_run_setup(self):
        database = Path(self.temp.name) / "study.sqlite3"
        with sqlite3.connect(database) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0], 4800)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM settings WHERE key='auth_hash'").fetchone()[0], 0)

        instance = server.StudyServer(("127.0.0.1", 0), server.Handler)
        self.port = instance.server_address[1]
        thread = threading.Thread(target=instance.serve_forever, daemon=True)
        thread.start()
        try:
            status, body, _ = self.request("GET", "/api/status")
            self.assertEqual(status, 200)
            self.assertTrue(body["setup_required"])
            status, _, _ = self.request("POST", "/api/setup", {"password": "testing-123"})
            self.assertEqual(status, 200)
            status, _, cookie = self.request("POST", "/api/login", {"password": "testing-123"})
            self.assertEqual(status, 200)
            self.assertIn("sid=", cookie)
            status, body, _ = self.request("POST", "/api/app-settings", {"network_mode": "external"}, cookie)
            self.assertEqual(status, 200)
            self.assertEqual(body["network_mode"], "external")
        finally:
            instance.shutdown()
            instance.server_close()

    def test_content_update_backs_up_and_preserves_user_data(self):
        database = Path(self.temp.name) / "study.sqlite3"
        with sqlite3.connect(database) as conn:
            question_id = conn.execute("SELECT id FROM questions ORDER BY id LIMIT 1").fetchone()[0]
            original_stem = conn.execute("SELECT stem FROM questions WHERE id=?", (question_id,)).fetchone()[0]
            conn.execute("UPDATE questions SET stem='stale bundled content' WHERE id=?", (question_id,))
            conn.execute("UPDATE settings SET value='0.9.0' WHERE key='content_version'")
            conn.execute(
                "INSERT INTO settings(key,value) VALUES('auth_hash',?)",
                (password_hash("personal-password"),),
            )
            conn.execute(
                "INSERT INTO question_notes(question_id,note,updated_at) VALUES(?,?,?)",
                (question_id, "my private note", "2026-10-17T12:00:00+08:00"),
            )
            conn.commit()

        server.prepare_database()

        backups = list((Path(self.temp.name) / "backups").glob("before-content-update-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        with sqlite3.connect(backups[0]) as backup:
            self.assertEqual(
                backup.execute("SELECT stem FROM questions WHERE id=?", (question_id,)).fetchone()[0],
                "stale bundled content",
            )
        with sqlite3.connect(database) as conn:
            restored_stem = conn.execute(
                "SELECT stem FROM questions WHERE id=?", (question_id,)
            ).fetchone()[0]
            self.assertEqual(restored_stem, original_stem)
            self.assertEqual(
                conn.execute("SELECT note FROM question_notes WHERE question_id=?", (question_id,)).fetchone()[0],
                "my private note",
            )
            content_version = conn.execute(
                "SELECT value FROM settings WHERE key='content_version'"
            ).fetchone()[0]
            self.assertEqual(content_version, server.APP_VERSION)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM settings WHERE key='auth_hash'").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
