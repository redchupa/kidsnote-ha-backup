import io
import json
import os
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch
from zipfile import ZipFile

from app import BackupController, notify_home_assistant, read_options
from pacing import RequestPacer
from viewer import create_app


class ViewerTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.viewer = create_app(self.root).test_client()
        report = self.root / "children" / "1" / "reports" / "2"
        report.mkdir(parents=True)
        (report.parents[1] / "profile.json").write_text('{"name": "테스트 자녀"}', encoding="utf-8")
        (report / "photo.png").write_bytes(b"0123456789")
        (report / "comment_7_image.png").write_bytes(b"1234567890")
        (report / "note.txt").write_text("Only allow listed attachments", encoding="utf-8")
        (report / "mock.html").write_text("<script>alert(1)</script>", encoding="utf-8")
        with ZipFile(report / "plan.docx", "w") as archive:
            archive.writestr("word/document.xml", (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body><w:p><w:r><w:t>11월 25일 견학</w:t></w:r></w:p>'
                '<w:p><w:r><w:t>우비 보내주세요~</w:t></w:r></w:p></w:body></w:document>'
            ))
        (report / "meta.json").write_text(json.dumps({
            "title": "산책 기록", "date_written": "2026-11-18", "author": {"type": "teacher", "name": "교사"},
            "content": "<p>나뭇잎을 모았어요.</p><p>내일 수건 보내주세요~</p>",
            "sleep_hour": "sleep_well",
            "_local_attachments": [
                {"file": "photo.png", "original_name": "사진.png"},
                {"file": "plan.docx", "original_name": "일정표.docx"},
                {"file": "mock.html", "original_name": "첨부.html"},
            ],
            "_comments": [
                {"id": 7, "author": {"type": "teacher"}, "content": "가족 사진 부탁드립니다~",
                 "_local_attachments": [{"file": "comment_7_image.png"}]},
                {"author": {"type": "parent"}, "content": "색연필 보내주세요~"},
            ],
        }), encoding="utf-8")
        self.admin = {"X-Remote-User-Groups": "system-admin"}

    def get(self, path, extra_headers=None):
        return self.viewer.get(path, headers={**self.admin, **(extra_headers or {})},
                               environ_overrides={"REMOTE_ADDR": "172.30.32.2"})

    def post(self, path, payload, extra_headers=None):
        return self.viewer.post(path, json=payload, headers={**self.admin, **(extra_headers or {})},
                                environ_overrides={"REMOTE_ADDR": "172.30.32.2"})

    def test_ingress_requires_proxy_and_admin(self):
        self.assertEqual(self.viewer.get("/", headers=self.admin).status_code, 403)
        self.assertEqual(self.get("/", {"X-Remote-User-Groups": "system-users"}).status_code, 403)
        with self.get("/") as response:
            self.assertEqual(response.status_code, 200)
            self.assertIn("키즈노트 보관함", response.get_data(as_text=True))
            self.assertEqual(response.headers["Cache-Control"], "no-store")
            self.assertIn("frame-ancestors 'self'", response.headers["Content-Security-Policy"])

    def test_existing_archive_search_and_readable_detail(self):
        data = self.get("/api/catalog", {}).json
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["months"], ["2026-11"])
        self.assertEqual(data["owners"], [{"id": "1", "name": "테스트 자녀"}])
        self.assertEqual(self.get("/api/catalog?q=가족").json["total"], 1)
        self.assertEqual(self.get("/api/catalog?owner=4").json["items"], [])
        self.assertEqual(self.get("/api/catalog?month=2026-10").json["items"], [])
        self.assertEqual(data["items"][0]["cover"], "photo.png")
        detail = self.get("/api/item?key=reports/1/2").json
        self.assertEqual(detail["fields"], [{"label": "수면", "value": "잠을 푹 잤어요"}])
        self.assertIn("나뭇잎을 모았어요.\n내일", detail["body"])
        self.assertEqual(detail["comments"][0]["attachments"][0]["file"], "comment_7_image.png")
        self.assertIn("11월 25일 견학", detail["attachments"][1]["text"])

    def test_board_uses_teacher_documents_and_links_local_source(self):
        with patch("viewer.supplies.today_kst", return_value=date(2026, 11, 19)):
            board = self.get("/api/board").json
        self.assertEqual(board["today"], "2026-11-19")
        self.assertEqual(board["events"][0]["start"], "2026-11-25")
        self.assertEqual(board["events"][0]["page_id"], "reports/1/2")
        requests = {entry["text"] for entry in board["requests"]}
        self.assertIn("우비 보내주세요~", requests)
        self.assertIn("가족 사진 부탁드립니다~", requests)
        self.assertIn("내일 수건 보내주세요~", requests)
        self.assertNotIn("색연필 보내주세요~", requests)

    def test_range_and_safe_content_delivery(self):
        with self.get("/media/reports/1/2/photo.png", {"Range": "bytes=2-4"}) as response:
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.data, b"234")
        with self.get("/media/reports/1/2/mock.html") as response:
            self.assertTrue(response.headers["Content-Disposition"].startswith("attachment"))
            self.assertEqual(response.mimetype, "application/octet-stream")
        self.assertEqual(self.get("/media/reports/1/2/note.txt").status_code, 404)
        self.assertEqual(self.get("/api/item?key=../../status.json").status_code, 404)
        self.assertEqual(self.get("/media/reports/1/2/%2e%2e%2foptions.json").status_code, 404)

    def test_document_text_is_searchable_in_catalog(self):
        # "견학" and "우비" exist only inside attached plan.docx, not in body/title/comments.
        self.assertEqual(self.get("/api/catalog?q=견학").json["total"], 1)
        self.assertEqual(self.get("/api/catalog?q=우비").json["total"], 1)

    def test_settings_persistence_and_validation(self):
        initial = self.get("/api/settings").json
        self.assertEqual(initial, {"title": "키즈노트 보관함", "theme": "auto", "default_tab": "reports"})
        updated = self.post("/api/settings", {
            "title": "  민준이 성장일기  ", "theme": "cute", "default_tab": "calendar",
        })
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json, {"title": "민준이 성장일기", "theme": "cute", "default_tab": "calendar"})
        self.assertEqual(self.get("/api/settings").json["theme"], "cute")
        # Empty title reverts to default
        reverted = self.post("/api/settings", {"title": "   ", "theme": "futuristic"})
        self.assertEqual(reverted.status_code, 200)
        self.assertEqual(reverted.json["title"], "키즈노트 보관함")
        self.assertEqual(reverted.json["theme"], "futuristic")
        # Invalid theme / tab rejected
        self.assertEqual(self.post("/api/settings", {"theme": "invalid"}).status_code, 400)
        self.assertEqual(self.post("/api/settings", {"default_tab": "unknown"}).status_code, 400)

    def test_integrated_calendar_returns_events_and_records(self):
        menu = self.root / "centers" / "9" / "menus" / "2026-11-18"
        menu.mkdir(parents=True)
        (menu / "meta.json").write_text(json.dumps({
            "date_menu": "2026-11-18", "lunch": "미역국, 불고기", "_local_attachments": [],
        }), encoding="utf-8")
        with patch("viewer.supplies.today_kst", return_value=date(2026, 11, 19)):
            cal = self.get("/api/calendar?month=2026-11").json
        self.assertEqual(cal["month"], "2026-11")
        self.assertEqual(len(cal["events"]), 1)
        self.assertEqual(cal["events"][0]["start"], "2026-11-25")
        kinds = {rec["kind"] for rec in cal["records"]}
        self.assertEqual(kinds, {"reports", "menus"})
        report_rec = next(r for r in cal["records"] if r["kind"] == "reports")
        self.assertEqual(report_rec["date"], "2026-11-18")
        self.assertEqual(report_rec["owner_name"], "테스트 자녀")
        # Child filter excludes other children's reports and their report-sourced events, but keeps center menus
        with patch("viewer.supplies.today_kst", return_value=date(2026, 11, 19)):
            filtered = self.get("/api/calendar?month=2026-11&owner=999").json
        self.assertEqual([r["kind"] for r in filtered["records"]], ["menus"])
        self.assertEqual(filtered["events"], [])
        self.assertEqual(self.get("/api/calendar?month=2026-99").status_code, 400)

    def test_supplies_checklist_persistence(self):
        item_id = "reports/1/2|내일 수건 보내주세요~"
        res = self.post("/api/supplies/check", {"id": item_id, "checked": True})
        self.assertEqual(res.status_code, 200)
        self.assertIn(item_id, res.json["checked"])
        with patch("viewer.supplies.today_kst", return_value=date(2026, 11, 19)):
            board = self.get("/api/board").json
        self.assertIn(item_id, board["checked"])
        res2 = self.post("/api/supplies/check", {"id": item_id, "checked": False})
        self.assertNotIn(item_id, res2.json["checked"])
        self.assertEqual(self.post("/api/supplies/check", {"id": "", "checked": True}).status_code, 400)

    def test_bulk_zip_attachment_download(self):
        with self.get("/media-zip/reports/1/2") as response:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "application/zip")
            with ZipFile(io.BytesIO(response.data)) as bundle:
                names = set(bundle.namelist())
                self.assertIn("사진.png", names)
                self.assertIn("일정표.docx", names)
                self.assertIn("comment_7_image.png", names)
                self.assertNotIn("note.txt", names)
        self.assertEqual(self.get("/media-zip/reports/1/999").status_code, 404)

    def test_manual_backup_trigger_and_cooldown(self):
        # Standalone viewer without controller
        self.assertFalse(self.get("/api/backup/status").json["can_trigger"])
        self.assertEqual(self.post("/api/backup/trigger", {}).status_code, 503)

        # Viewer with BackupController
        pacer = RequestPacer(Event())
        controller = BackupController(pacer)
        client = create_app(self.root, backup_controller=controller).test_client()
        hdrs = self.admin
        env = {"REMOTE_ADDR": "172.30.32.2"}

        self.assertTrue(client.get("/api/backup/status", headers=hdrs, environ_overrides=env).json["can_trigger"])
        # Non-JSON POST rejected for CSRF defense
        r_bad = client.post("/api/backup/trigger", data="x=1", headers=hdrs, environ_overrides=env)
        self.assertEqual(r_bad.status_code, 400)

        r1 = client.post("/api/backup/trigger", json={}, headers=hdrs, environ_overrides=env)
        self.assertEqual(r1.status_code, 202)
        self.assertTrue(controller.wake.is_set())

        # Duplicate click before wake is consumed -> 409
        r_dup = client.post("/api/backup/trigger", json={}, headers=hdrs, environ_overrides=env)
        self.assertEqual(r_dup.status_code, 409)
        controller.wake.clear()

        # While running -> 409
        controller.set_running(True)
        r2 = client.post("/api/backup/trigger", json={}, headers=hdrs, environ_overrides=env)
        self.assertEqual(r2.status_code, 409)
        controller.set_running(False)

        # During cooldown -> 429
        pacer.cooldown_until = time.time() + 600
        r3 = client.post("/api/backup/trigger", json={}, headers=hdrs, environ_overrides=env)
        self.assertEqual(r3.status_code, 429)

    def test_ha_notification_and_option(self):
        cfg = self.root / "options.json"
        cfg.write_text('{"notify_ha": true}', encoding="utf-8")
        self.assertTrue(read_options(cfg)["notify_ha"])

        with patch.dict(os.environ, {"SUPERVISOR_TOKEN": "test-token"}, clear=False):
            fake_resp = MagicMock(status_code=200)
            with patch("app.requests.Session.post", return_value=fake_resp) as post_mock:
                self.assertTrue(notify_home_assistant({"state": "success", "records": 3, "new_attachments": 2, "errors": 0}))
                post_mock.assert_called_once()
                self.assertFalse(notify_home_assistant({"state": "success", "records": 0}))

    def test_video_only_catalog_filter(self):
        # Existing report 2 has no video
        self.assertEqual(self.get("/api/catalog?kind=reports&video=1").json["total"], 0)
        self.assertFalse(self.get("/api/catalog?kind=reports").json["items"][0]["has_video"])

        # Add report 3 with a video attachment
        report3 = self.root / "children" / "1" / "reports" / "3"
        report3.mkdir(parents=True)
        (report3 / "clip.mp4").write_bytes(b"fake-mp4-data")
        (report3 / "meta.json").write_text(json.dumps({
            "title": "동영상 알림장", "date_written": "2026-11-19",
            "content": "즐겁게 뛰어놀았어요.",
            "_local_attachments": [{"file": "clip.mp4", "original_name": "놀이.mp4"}],
            "_comments": [],
        }), encoding="utf-8")

        filtered = self.get("/api/catalog?kind=reports&video=1").json
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["items"][0]["key"], "reports/1/3")
        self.assertTrue(filtered["items"][0]["has_video"])

        # All reports returns both (2 total)
        self.assertEqual(self.get("/api/catalog?kind=reports").json["total"], 2)
