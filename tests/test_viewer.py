import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

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
