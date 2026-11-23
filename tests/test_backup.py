import json
import logging
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from threading import Event
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import requests

from app import list_children, read_options, run_once
from client import BackupError, KidsnoteClient
from kidsnote_auth import KIDSNOTE_BASE


def response(body=None, status=200, **headers):
    result = requests.Response()
    result.status_code = status
    result.headers.update(headers)
    if isinstance(body, bytes):
        result.raw = BytesIO(body)
    else:
        result._content = json.dumps(body).encode()
        result._content_consumed = True
    return result


class KidsnoteStub:
    def __init__(self):
        self.api_calls = []
        self.media_calls = []
        self.signature = "initial"
        self.video_ok = False
        self.comment_media = False
        self.comment = "First comment"

    def __call__(self, url, *, headers, **kwargs):
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        path = parsed.path
        if parsed.hostname == "cdn.example.test":
            self.media_calls.append((path, headers.get("Cookie")))
            if path == "/clip.mp4" and not self.video_ok:
                return response(b"incomplete", **{"Content-Length": "99"})
            return response(b"test media")
        self.api_calls.append(path)
        if path == "/api/v1/me/children/":
            return response({"children": [
                {"id": 1, "enrollment": [{"center": {"id": 12}}]},
                {"id": 2, "enrollment": [{"center_id": 12}]},
            ]})
        if path == "/api/v1_2/children/1/reports/":
            if query.get("page") == ["opaque+token="]:
                return response({"results": [{"id": 31}, {"id": 32}]})
            return response({"results": [{"id": 31}], "next": "opaque+token="})
        if path in ("/api/v1_2/children/2/reports/", "/api/v1/children/2/albums/"):
            return response({"results": []})
        if path == "/api/v1_2/reports/31/":
            return response({
                "id": 31, "content": "Synthetic report", "sleep": "example",
                "attached_images": [{"id": 100, "original": None,
                                     "url": f"https://cdn.example.test/photo.jpg?s={self.signature}"}],
                "attached_video": {"original": "https://cdn.example.test/clip.mp4"},
                "attached_files": [{"original": "https://cdn.example.test/file",
                                    "original_file_name": "sample.pdf"}],
            })
        if path == "/api/v1_2/reports/32/":
            return response({"id": 32, "body": "Another record"})
        if path == "/api/v1/reports/31/comments/":
            if "page" in query:
                return response({"results": [{"id": 102, "content": "Second comment"}]})
            comment = {"id": 101, "content": self.comment}
            if self.comment_media:
                comment["attached_images"] = ["https://cdn.example.test/comment.jpg"]
            return response({"results": [comment], "next": "?page=2"})
        if path == "/api/v1/reports/32/comments/":
            return response({"results": []})
        if path == "/api/v1/children/1/albums/":
            return response({"results": [{"id": 41, "title": "Synthetic album"}]})
        if path == "/api/v1/albums/41/comments/":
            return response(status=404)
        if path == "/api/v1/centers/12/notices/":
            return response({"results": [{"id": 51, "content": "Synthetic notice"}]})
        if path == "/api/v1/notices/51/comments/":
            return response({"results": []})
        if path == "/api/v1/centers/12/menu/":
            return response({"results": [{
                "id": 61, "date_menu": "2026-02-03", "lunch": "Example",
                "lunch_img": {"original": "https://cdn.example.test/lunch.jpg"},
            }]})
        raise AssertionError(f"Unexpected route: {path}")


class BackupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.options_path = self.root / "fake-options.json"
        self.options_path.write_text(json.dumps({
            "include_schedules": False, "include_medications": False,
            "include_return_homes": False, "include_temperatures": False,
        }), encoding="utf-8")
        self.options = read_options(self.options_path)
        self.client = KidsnoteClient()
        self.addCleanup(self.client.session.close)
        wait = patch.object(self.client.pacer, "wait")
        wait.start()
        self.addCleanup(wait.stop)
        self.client.token = "SyntheticCookie"
        self.stop = Event()

    def run_backup(self):
        with self.assertLogs(level=logging.INFO) as logs:
            state = run_once(self.client, self.root, self.options, self.stop)
        self.assertNotIn(self.client.token or "SyntheticCookie", "\n".join(logs.output))
        return state

    def load(self, path):
        return json.loads((self.root / path).read_text(encoding="utf-8"))

    def test_partial_then_resume_with_fresh_signature_and_comments(self):
        server = KidsnoteStub()
        with patch.object(self.client.session, "get", side_effect=server):
            self.assertEqual(self.run_backup(), "partial")
            self.assertFalse((self.root / "children/1/reports/31/meta.json").exists())
            self.assertFalse(list(self.root.rglob("*.part")))
            self.assertFalse(list(self.root.rglob("video_*.mp4")))
            self.assertEqual(self.load("status.json")["errors"], 1)
            self.assertEqual(self.load("children/1/reports/32/meta.json")["body"], "Another record")
            self.assertIsNone(self.load("children/1/albums/41/meta.json")["_comments"])
            self.assertEqual(self.load("centers/12/notices/51/meta.json")["_comments"], [])
            self.assertEqual(self.load("centers/12/menus/2026-02-03_61/meta.json")["lunch"], "Example")
            self.assertEqual(server.api_calls.count("/api/v1/centers/12/menu/"), 1)

            server.video_ok = True
            server.signature = "renewed"
            server.comment = "Edited comment"
            server.media_calls.clear()
            self.assertEqual(self.run_backup(), "success")
            self.assertEqual(server.media_calls, [("/clip.mp4", None), ("/file", None)])
            record = self.load("children/1/reports/31/meta.json")
            self.assertEqual(record["sleep"], "example")
            self.assertEqual([c["content"] for c in record["_comments"]], ["Edited comment", "Second comment"])
            self.assertEqual(record["_local_attachments"][2]["original_name"], "sample.pdf")
            status = self.load("status.json")
            self.assertEqual(status["new_attachments"], 2)
            self.assertEqual(status["last_success_at"], status["finished_at"])
            report = self.root / "children/1/reports/31"
            self.assertEqual((report / "note.txt").read_text(), "Synthetic report")
            self.assertTrue(all((report / media["file"]).is_file() for media in record["_local_attachments"]))

            self.stop.set()
            self.assertEqual(self.run_backup(), "stopped")
            self.assertEqual(self.load("status.json")["last_success_at"], status["last_success_at"])

    def test_report_only_with_one_child_selected(self):
        self.options.update({
            "child_ids": {"2"}, "include_albums": False,
            "include_notices": False, "include_menus": False,
        })
        server = KidsnoteStub()
        with patch.object(self.client.session, "get", side_effect=server):
            self.assertEqual(self.run_backup(), "success")
        self.assertEqual(server.api_calls, ["/api/v1/me/children/", "/api/v1_2/children/2/reports/"])

    def test_comment_attachments_are_saved_and_reused(self):
        server = KidsnoteStub()
        server.video_ok = server.comment_media = True
        with patch.object(self.client.session, "get", side_effect=server):
            self.assertEqual(self.run_backup(), "success")
            report = self.root / "children/1/reports/31"
            metadata = self.load("children/1/reports/31/meta.json")
            asset = metadata["_comments"][0]["_local_attachments"][0]["file"]
            self.assertTrue(asset.startswith("comment_101_image_"))
            self.assertEqual((report / asset).read_bytes(), b"test media")
            server.media_calls.clear()
            self.assertEqual(self.run_backup(), "success")
            self.assertEqual(server.media_calls, [])

    def test_expired_cookie_logs_in_and_reuses_session(self):
        self.options.update({"username": "synthetic", "password": "example"})
        children = {"results": [{"id": 1}]}
        replies = [response(status=401), response(children), response(children)]
        with (patch.object(self.client.session, "get", side_effect=replies) as get,
              patch("app.login", return_value="NewCookie") as login):
            self.assertEqual(list_children(self.client, self.options), children["results"])
            self.assertEqual(list_children(self.client, self.options), children["results"])
        login.assert_called_once_with("synthetic", "example", pacer=self.client.pacer)
        self.assertEqual(get.call_args_list[1].kwargs["headers"]["Cookie"], "sessionid=NewCookie")

    def test_token_does_not_follow_cdn_redirect(self):
        redirected = response(status=302, Location="https://cdn.example.test/final.jpg")
        with patch.object(self.client.session, "get", side_effect=[redirected, response(b"media")]) as get:
            with self.client.get(f"{KIDSNOTE_BASE}/media/photo.jpg", media=True):
                pass
        self.assertEqual(get.call_args_list[0].kwargs["headers"]["Cookie"], "sessionid=SyntheticCookie")
        self.assertNotIn("Cookie", get.call_args_list[1].kwargs["headers"])

    def test_external_api_pagination_is_rejected_before_request(self):
        external = response({"results": [], "next": "https://other.example.test/records"})
        with patch.object(self.client.session, "get", return_value=external) as get:
            with self.assertRaisesRegex(BackupError, "outside its own origin"):
                list(self.client.items("/api/v1/me/children/"))
        self.assertEqual(get.call_count, 1)

    def test_pagination_cycle_is_reported_instead_of_succeeding(self):
        repeated = response({"results": [], "next": "loop"})
        with patch.object(self.client.session, "get", return_value=repeated) as get:
            with self.assertRaisesRegex(BackupError, "did not terminate"):
                list(self.client.items("/api/v1/me/children/"))
        self.assertEqual(get.call_count, 2)

    def test_numeric_next_pages_are_passed_to_kidsnote(self):
        replies = [
            response({"results": [{"id": 1}], "next": 2}),
            response({"results": [{"id": 2}], "next": None}),
        ]
        with patch.object(self.client.session, "get", side_effect=replies) as get:
            items = list(self.client.items("/api/v1/centers/1/menu/", page_size=1))
        self.assertEqual(items, [{"id": 1}, {"id": 2}])
        query = parse_qs(urlsplit(get.call_args_list[1].args[0]).query)
        self.assertEqual(query, {"page_size": ["1"], "page": ["2"]})

    def test_configuration_validation(self):
        self.options_path.write_text("{}", encoding="utf-8")
        defaults = read_options(self.options_path)
        self.assertEqual(defaults["interval_hours"], 24)
        for key in ("include_albums", "include_notices", "include_menus",
                    "include_schedules", "include_medications", "include_return_homes", "include_temperatures"):
            self.assertTrue(defaults[key])
        self.assertFalse(defaults["notify_ha"])
        self.options_path.write_text('{"interval_hours": 0}', encoding="utf-8")
        with self.assertRaisesRegex(BackupError, "interval_hours"):
            read_options(self.options_path)
        self.options_path.write_text('{"child_ids": [true]}', encoding="utf-8")
        with self.assertRaisesRegex(BackupError, "child_ids"):
            read_options(self.options_path)

    def test_child_id_lists_from_the_ui_and_existing_installations(self):
        for ids in ([1234567, 7890123], ["1234567", "7890123"], [1234567, "7890123"]):
            with self.subTest(ids=ids):
                self.options_path.write_text(json.dumps({"child_ids": ids}), encoding="utf-8")
                self.assertEqual(read_options(self.options_path)["child_ids"], {"1234567", "7890123"})
        self.options_path.write_text('{"child_ids": 1234567}', encoding="utf-8")
        with self.assertRaisesRegex(BackupError, r"child_ids: \[1234567\]"):
            read_options(self.options_path)

    def test_notices_and_menus_limited_to_earliest_report_date(self):
        self.options.update({"include_albums": False})
        fetched_pages = []

        def fake_get(url, *, headers, **kwargs):
            parsed = urlsplit(url)
            query = parse_qs(parsed.query)
            path = parsed.path
            fetched_pages.append((path, query.get("page")))
            if path == "/api/v1/me/children/":
                return response({"children": [
                    {"id": 1, "enrollment": [{"center_id": 12}]},
                    {"id": 2, "enrollment": [{"center_id": 12}]},
                ]})
            if path == "/api/v1_2/children/1/reports/":
                return response({"results": [
                    {"id": 10, "date_written": "2026-05-10"},
                    {"id": 11, "date_written": "2026-03-02"},
                ]})
            if path == "/api/v1_2/children/2/reports/":
                return response({"results": [
                    {"id": 12, "date_written": "2026-04-01"},
                ]})
            if path.startswith("/api/v1_2/reports/"):
                return response({"content": "report"})
            if path.endswith("/comments/"):
                return response({"results": []})
            if path == "/api/v1/centers/12/notices/":
                if query.get("page") == ["2"]:
                    raise AssertionError("Should have stopped pagination after consecutive old notices")
                old_notices = [
                    {"id": 200 + i, "created": "2026-02-01T09:00:00+09:00", "content": "old"}
                    for i in range(10)
                ]
                return response({
                    "results": [
                        # Pinned old notice at top of page 1 must not abort newer notices below it
                        {"id": 99, "created": "2025-12-01T09:00:00+09:00", "content": "pinned old"},
                        {"id": 100, "created": "2026-03-02T09:00:00+09:00", "content": "on boundary"},
                        {"id": 101, "created": "2026-03-15T09:00:00+09:00", "content": "after boundary"},
                        *old_notices,
                    ],
                    "next": 2,
                })
            if path == "/api/v1/centers/12/menu/":
                return response({"results": [
                    {"id": 301, "date_menu": "2026-03-02", "lunch": "Boundary lunch"},
                    {"id": 302, "date_menu": "2026-03-01", "lunch": "Too old lunch"},
                ]})
            raise AssertionError(f"Unexpected route: {path}")

        with patch.object(self.client.session, "get", side_effect=fake_get):
            self.assertEqual(self.run_backup(), "success")

        # Notices: 100 and 101 saved; pinned 99 and 200..209 skipped; page 2 never fetched
        self.assertTrue((self.root / "centers/12/notices/100/meta.json").is_file())
        self.assertTrue((self.root / "centers/12/notices/101/meta.json").is_file())
        self.assertFalse((self.root / "centers/12/notices/99/meta.json").exists())
        self.assertFalse((self.root / "centers/12/notices/200/meta.json").exists())
        # Menus: 2026-03-02 saved; 2026-03-01 skipped
        self.assertTrue((self.root / "centers/12/menus/2026-03-02_301/meta.json").is_file())
        self.assertFalse((self.root / "centers/12/menus/2026-03-01_302/meta.json").exists())

    def test_center_id_recovery_from_dict_enrollment_and_report_metadata(self):
        self.options.update({"include_albums": False})

        def fake_get(url, *, headers, **kwargs):
            path = urlsplit(url).path
            if path == "/api/v1/me/children/":
                return response({"children": [
                    {"id": 1, "enrollment": {"center_id": 21}},
                    {"id": 2},  # no enrollment on child; center comes from report detail
                ]})
            if path == "/api/v1_2/children/1/reports/":
                return response({"results": [{"id": 10, "date_written": "2026-05-01"}]})
            if path == "/api/v1_2/children/2/reports/":
                return response({"results": [{"id": 20, "date_written": "2026-05-01"}]})
            if path == "/api/v1_2/reports/10/":
                return response({"content": "r1"})
            if path == "/api/v1_2/reports/20/":
                return response({"content": "r2", "center": {"id": 22}})
            if path.endswith("/comments/"):
                return response({"results": []})
            if path in ("/api/v1/centers/21/notices/", "/api/v1/centers/22/notices/"):
                return response({"results": [{"id": 1, "created": "2026-05-02T09:00:00+09:00", "content": "n"}]})
            if path in ("/api/v1/centers/21/menu/", "/api/v1/centers/22/menu/"):
                return response({"results": [{"id": 1, "date_menu": "2026-05-02", "lunch": "m"}]})
            raise AssertionError(f"Unexpected route: {path}")

        with patch.object(self.client.session, "get", side_effect=fake_get):
            self.assertEqual(self.run_backup(), "success")

        self.assertTrue((self.root / "centers/21/notices/1/meta.json").is_file())
        self.assertTrue((self.root / "centers/21/menus/2026-05-02_1/meta.json").is_file())
        self.assertTrue((self.root / "centers/22/notices/1/meta.json").is_file())
        self.assertTrue((self.root / "centers/22/menus/2026-05-02_1/meta.json").is_file())

    def test_first_backup_logs_estimated_time_from_count(self):
        from archive import format_duration

        self.assertEqual(format_duration(20), "1분 미만")
        self.assertEqual(format_duration(600), "약 10분")
        self.assertEqual(format_duration(5400), "약 1시간 30분")

        self.options.update({"include_albums": False, "include_notices": False, "include_menus": False})

        def fake_get(url, *, headers, **kwargs):
            path = urlsplit(url).path
            if path == "/api/v1/me/children/":
                return response({"children": [{"id": 1}]})
            if path == "/api/v1_2/children/1/reports/":
                return response({"count": 120, "results": [{"id": 10, "date_written": "2026-05-01"}]})
            if path == "/api/v1_2/reports/10/":
                return response({"content": "r1"})
            if path.endswith("/comments/"):
                return response({"results": []})
            raise AssertionError(f"Unexpected route: {path}")

        with patch.object(self.client.session, "get", side_effect=fake_get), self.assertLogs(level=logging.INFO) as logs:
            self.assertEqual(run_once(self.client, self.root, self.options, self.stop), "success")
        joined = "\n".join(logs.output)
        self.assertIn("첫 백업을 시작합니다", joined)
        self.assertIn("[알림장] 총 120건 확인됨", joined)
        self.assertIn("정도 소요될 수 있습니다", joined)

        # Bogus count smaller than page results (e.g. unread badge count) must be ignored
        bogus = response({"count": 1, "results": [{"id": 1}, {"id": 2}]})
        with patch.object(self.client.session, "get", return_value=bogus):
            list(self.client.items("/api/v1_2/children/1/reports/"))
        self.assertIsNone(self.client.last_count)

    def test_resume_from_checkpoint_after_mid_backup_stop(self):
        calls = []
        stop_after_first_album = True

        def fake_get(url, *, headers, **kwargs):
            path = urlsplit(url).path
            calls.append(path)
            if path == "/api/v1/me/children/":
                return response({"children": [{"id": 1, "enrollment": [{"center_id": 12}]}]})
            if path == "/api/v1_2/children/1/reports/":
                return response({"results": [{"id": 10, "date_written": "2026-05-01"}]})
            if path == "/api/v1_2/reports/10/":
                return response({"content": "r1"})
            if path == "/api/v1/children/1/albums/":
                return response({"results": [
                    {"id": 41, "title": "Album 1"},
                    {"id": 42, "title": "Album 2"},
                ]})
            if path == "/api/v1/albums/41/comments/":
                if stop_after_first_album:
                    self.stop.set()  # interrupt right after album 41 finishes saving
                return response({"results": []})
            if path.endswith("/comments/"):
                return response({"results": []})
            if path == "/api/v1/centers/12/notices/":
                return response({"results": [{"id": 51, "created": "2026-05-02T09:00:00+09:00", "content": "n1"}]})
            if path == "/api/v1/centers/12/menu/":
                return response({"results": [{"id": 61, "date_menu": "2026-05-02", "lunch": "m1"}]})
            raise AssertionError(f"Unexpected route: {path}")

        # Run 1: stops right after album 41
        with patch.object(self.client.session, "get", side_effect=fake_get):
            self.assertEqual(self.run_backup(), "stopped")
        self.assertTrue((self.root / "checkpoint.json").is_file())
        self.assertTrue((self.root / "children/1/reports/10/meta.json").is_file())
        self.assertTrue((self.root / "children/1/albums/41/meta.json").is_file())
        self.assertFalse((self.root / "children/1/albums/42/meta.json").exists())
        self.assertFalse((self.root / "centers/12/notices/51/meta.json").exists())

        # Run 2: resume cleanly without re-fetching completed reports or album 41
        stop_after_first_album = False
        self.stop.clear()
        calls.clear()
        with patch.object(self.client.session, "get", side_effect=fake_get):
            self.assertEqual(self.run_backup(), "success")
        self.assertNotIn("/api/v1_2/children/1/reports/", calls)
        self.assertNotIn("/api/v1/albums/41/comments/", calls)
        self.assertTrue((self.root / "children/1/albums/42/meta.json").is_file())
        self.assertTrue((self.root / "centers/12/notices/51/meta.json").is_file())
        self.assertTrue((self.root / "centers/12/menus/2026-05-02_61/meta.json").is_file())
        self.assertFalse((self.root / "checkpoint.json").exists())

    def test_schedules_medications_return_homes_and_temperatures(self):
        self.options.update({
            "include_albums": False, "include_notices": False, "include_menus": False,
            "include_schedules": True, "include_medications": True,
            "include_return_homes": True, "include_temperatures": True,
        })

        def fake_get(url, *, headers, **kwargs):
            parsed = urlsplit(url)
            query = parse_qs(parsed.query)
            path = parsed.path
            if parsed.hostname == "cdn.example.test":
                return response(b"img-bytes")
            if path == "/api/v1/me/children/":
                return response({"children": [
                    {"id": 1, "enrollment": [{"center_id": 12, "belong_to_class": 34}]},
                ]})
            if path == "/api/v1_2/children/1/reports/":
                return response({"results": [{"id": 10, "date_written": "2026-11-01"}]})
            if path == "/api/v1_2/reports/10/":
                return response({"content": "first report"})
            if path.endswith("/comments/"):
                return response({"results": []})
            if path == "/api/v1_2/centers/12/medications":
                self.assertEqual(query.get("cls"), ["34"])
                self.assertEqual(query.get("child"), ["1"])
                return response({"results": [
                    {
                        "id": 701, "date_medicated": "2026-11-10", "symptom": "감기",
                        "items": [{"medicine_type": "시럽", "dosage": "5ml", "frequency": 1}],
                        "attached_image": {"id": 9, "original": "https://cdn.example.test/med.jpg"},
                    },
                    {"id": 700, "date_medicated": "2026-10-15", "symptom": "이전 기록"},
                ]})
            if path == "/api/v1_2/centers/12/return-home":
                self.assertEqual(query.get("cls"), ["34"])
                self.assertEqual(query.get("child"), ["1"])
                return response({"results": [
                    {
                        "id": 801, "datetime_requested": "2026-11-12T16:00:00Z",
                        "method": "자가용", "guardian_name": "어머니", "is_confirmed": True,
                    },
                    {"id": 800, "datetime_requested": "2026-10-01T16:00:00Z", "method": "도보"},
                ]})
            if path == "/api/v1/centers/12/children/temperatures":
                self.assertEqual(query.get("child_id"), ["1"])
                if query.get("measured_month") == ["2026-11"]:
                    return response([{"date": "2026-11-10", "temperature": 36.7}])
                return response([])
            if path == "/api/v1/centers/12/calendar":
                self.assertEqual(query.get("cls"), ["34"])
                if query.get("month_event") == ["2026-11"]:
                    return response([
                        {"id": 901, "date_event": "2026-11-20", "title": "가을 소풍", "content": "도시락 지참"},
                        {"id": None, "date_event": "2026-11-22", "title": "생일 축하", "content": ""},
                        {"id": 900, "date_event": "2026-10-20", "title": "이전 일정", "content": ""},
                    ])
                return response([])
            raise AssertionError(f"Unexpected route: {path}")

        with (patch.object(self.client.session, "get", side_effect=fake_get),
              patch("archive.datetime") as dt_mock):
            from datetime import datetime, timezone, timedelta
            dt_mock.now.return_value = datetime(2026, 11, 23, 12, 0, tzinfo=timezone(timedelta(hours=9)))
            dt_mock.fromisoformat = datetime.fromisoformat
            self.assertEqual(self.run_backup(), "success")

        # Medications: 701 saved (with attachment), 700 before 2026-11-01 skipped
        self.assertTrue((self.root / "children/1/medications/701/meta.json").is_file())
        self.assertEqual((self.root / "children/1/medications/701/note.txt").read_text(encoding="utf-8"), "감기")
        self.assertFalse((self.root / "children/1/medications/700/meta.json").exists())
        # Return-homes: 801 saved, 800 before 2026-11-01 skipped
        self.assertTrue((self.root / "children/1/return_homes/801/meta.json").is_file())
        self.assertFalse((self.root / "children/1/return_homes/800/meta.json").exists())
        # Temperatures: 2026-11 saved and recognized as complete for checkpoint resume
        from archive import has_complete_local_record
        self.assertTrue((self.root / "children/1/temperatures/2026-11/meta.json").is_file())
        self.assertTrue(has_complete_local_record(self.root / "children/1/temperatures/2026-11", "temperatures"))
        # Schedules: 901 and null-id birthday saved, 900 before 2026-11-01 skipped
        sched_dirs = sorted(p.name for p in (self.root / "centers/12/schedules").iterdir())
        self.assertEqual(len(sched_dirs), 2)
        self.assertIn("2026-11-20_901", sched_dirs)
        self.assertTrue(any(name.startswith("2026-11-22_e") for name in sched_dirs))


if __name__ == "__main__":
    unittest.main()
