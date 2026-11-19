import json
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import requests

from app import main, read_options, run_once
from archive import Archive, already_backed_up
from client import KidsnoteClient
from kidsnote_auth import KIDSNOTE_BASE, _request
from pacing import RequestPacer, Stopping, retry_after_seconds


class Clock:
    def __init__(self):
        self.elapsed = 0
        self.start = 1_800_000_000
        self.stopped = False

    def time(self):
        return self.start + self.elapsed

    def monotonic(self):
        return self.elapsed

    def wait(self, delay):
        if not self.stopped:
            self.elapsed += delay
        return self.stopped


def reply(status, **headers):
    result = requests.Response()
    result.status_code = status
    result.headers.update(headers)
    result._content = b"{}"
    result._content_consumed = True
    return result


class PacingTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        patcher = patch("pacing.time", SimpleNamespace(time=self.clock.time, monotonic=self.clock.monotonic))
        patcher.start()
        self.addCleanup(patcher.stop)
        jitter = patch("pacing.random.uniform", return_value=0)
        jitter.start()
        self.addCleanup(jitter.stop)
        self.pacer = RequestPacer(self.clock)
        self.client = KidsnoteClient(self.pacer)
        self.addCleanup(self.client.session.close)

    def test_login_api_media_redirect_and_retry_share_one_limit(self):
        times = []
        url = KIDSNOTE_BASE + "/api/v1/me/children/"
        photo = "https://cdn.example.test/photo.jpg"
        redirected = "https://media.example.test/photo.jpg"
        fake_login = MagicMock()
        fake_login.__enter__.return_value.status = 200
        fake_login.__enter__.return_value.read.return_value = b"{}"

        def auth_open(*args, **kwargs):
            times.append(self.clock.elapsed)
            return fake_login

        def get(*args, **kwargs):
            times.append(self.clock.elapsed)
            return next(responses)

        responses = iter([reply(200), reply(302, Location=redirected), reply(502), reply(200), reply(200)])
        with patch.object(self.client.session, "get", side_effect=get):
            _request(SimpleNamespace(open=auth_open), self.pacer, "GET", "/api/v1/second-factors/test/")
            with self.client.get(url):
                pass
            with self.client.get(photo, media=True):
                pass
            with self.client.get(url):
                pass
        self.assertEqual(times, [0, 1, 2, 4, 9, 11])

    def test_overload_pauses_the_entire_run_without_retrying(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "options.json"
            config.write_text("{}", encoding="utf-8")
            options = read_options(config)
            self.client.token = "SyntheticSession"
            for status in (429, 503):
                self.pacer.cooldown_until = 0
                with patch.object(self.client.session, "get", return_value=reply(
                    status, **{"Retry-After": "7200"},
                )) as get, self.assertLogs(level="INFO"):
                    self.assertEqual(run_once(self.client, root, options, Event()), "deferred")
                self.assertEqual(get.call_count, 1)
                saved = json.loads((root / "status.json").read_text(encoding="utf-8"))
                self.assertEqual(saved["retry_not_before"], self.clock.time() + 7200)

    def test_restart_does_not_bypass_a_saved_cooldown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "options.json"
            config.write_text("{}", encoding="utf-8")
            (root / "status.json").write_text(
                json.dumps({"retry_not_before": time.time() + 3600}), encoding="utf-8",
            )
            with (patch("app.KidsnoteClient") as client, patch("app.os.umask"),
                  patch("app.signal.signal"), self.assertLogs(level="WARNING")):
                self.assertEqual(main(["--once", "--options", str(config), "--archive", str(root)]), 1)
            client.assert_not_called()

    def test_retry_after_http_date_and_interruptible_waits(self):
        self.assertEqual(retry_after_seconds("Thu, 01 Jan 1970 01:00:00 GMT", 600), 3000)
        self.assertEqual(retry_after_seconds("invalid", 600), 0)
        self.clock.stopped = True
        with self.assertRaises(Stopping):
            self.pacer.wait()

    def test_wake_event_interrupts_schedule_but_respects_cooldown(self):
        wake = Event()
        wake.set()
        self.pacer.wait_until(self.clock.time() + 3600, wake=wake)
        self.assertEqual(self.clock.elapsed, 0)
        self.assertFalse(wake.is_set())

        # During cooldown, wake must not bypass cooldown_until
        self.pacer.cooldown_until = self.clock.time() + 10
        wake.set()
        self.pacer.wait_until(self.clock.time() + 3600, wake=wake)
        self.assertGreaterEqual(self.clock.elapsed, 10)
        self.assertFalse(wake.is_set())


class IncrementalTest(unittest.TestCase):
    def test_complete_unchanged_old_records_skip_api_detail_and_comments(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "31"
            folder.mkdir()
            photo = folder / "photo.jpg"
            photo.write_bytes(b"photo")
            (folder / "note.txt").write_text("original note", encoding="utf-8")
            record = {"id": 31, "modified": "2020-01-01T00:00:00Z", "num_comments": 1}
            (folder / "meta.json").write_text(json.dumps({
                **record, "_local_attachments": [],
                "_comments": [{"_local_attachments": [{"file": photo.name}]}],
            }), encoding="utf-8")
            client = Mock()
            client.items.return_value = iter([record])
            archive = Archive(client, root, Event())
            archive.collection("/reports/", root, "reports")
            self.assertEqual(archive.unchanged, 1)
            client.items.assert_called_once_with("/reports/")
            client.json.assert_not_called()
            self.assertFalse(already_backed_up(folder, {**record, "num_comments": 2}))
            photo.unlink()
            self.assertFalse(already_backed_up(folder, record))
