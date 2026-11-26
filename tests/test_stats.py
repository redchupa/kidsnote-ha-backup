import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import stats
from viewer import create_app


def report(day, hour, minute=0, text="", author="teacher", upload_day=None):
    upload = upload_day or day
    return {
        "date_written": day.isoformat(),
        "created": f"{upload.isoformat()}T{hour:02d}:{minute:02d}:00+09:00",
        "author": {"type": author, "name": "김선생"},
        "content": text,
    }


class StatsTest(unittest.TestCase):
    def setUp(self):
        self.today = date(2026, 11, 26)
        self.rows = []
        for offset in range(12):
            day = self.today - timedelta(days=offset + 1)
            self.rows.append((f"reports/1/{100 + offset}", report(
                day, 14, 5 * offset,
                f"오늘 하린이는 블록 놀이를 {offset}번 했어요. 점심을 맛있게 먹고 낮잠도 푹 잤어요.\n"
                f"{offset}번째 산책에서 나뭇잎을 주웠어요.",
            )))

    def run_rows(self, extra=()):
        rows = [(key, item, item["content"], date.fromisoformat(item["date_written"]))
                for key, item in [*self.rows, *extra]]
        return stats.analyze(rows, child_name="우하린", today=self.today, days=90)

    def test_baseline_and_unusual_hours(self):
        late = ("reports/1/900", report(self.today, 21, 30, "밤에 올라온 알림장입니다. 내용이 짧아요."))
        integrated = ("reports/1/901", report(self.today, 17, 10, "통합보육 시간에 작성했어요. 놀이를 했어요."))
        backlog = ("reports/1/902", report(self.today - timedelta(days=5), 14, 10, "며칠 뒤 몰아 쓴 알림장이에요.",
                                           upload_day=self.today))
        result = self.run_rows([late, integrated, backlog])["timing"]
        self.assertEqual(result["baseline"]["median"][:2], "14")
        flags = {entry["key"]: entry["tags"] for entry in result["flagged"]}
        self.assertIn("shift", flags["reports/1/900"])
        self.assertIn("after", flags["reports/1/900"])
        self.assertIn("extended", flags["reports/1/901"])
        self.assertEqual(flags["reports/1/902"], ["late"])
        self.assertNotIn("reports/1/100", flags)
        self.assertEqual(sum(result["hist"]), 15)

    def test_repeated_sentences_ignore_child_name(self):
        other = ("reports/1/950", report(self.today, 14, 0,
                                         "오늘 서준이는 퍼즐을 했어요. 점심을 맛있게 먹고 낮잠도 푹 잤어요."))
        result = self.run_rows([other])["repetition"]
        top = result["sentences"][0]
        self.assertEqual(top["count"], 13)
        self.assertIn("낮잠도 푹 잤어요", top["text"])
        self.assertGreater(result["average_ratio"], 0.3)

    def test_near_duplicate_pairs_and_parent_posts_skipped(self):
        same = "오늘은 친구들과 함께 신나게 미끄럼틀을 타고 모래놀이를 하며 즐거운 시간을 보냈어요."
        extra = [
            ("reports/1/960", report(self.today, 14, 0, same)),
            ("reports/1/961", report(self.today - timedelta(days=1), 14, 0, same + "!")),
            ("reports/1/962", report(self.today, 23, 0, "부모가 남긴 글", author="parent")),
        ]
        result = self.run_rows(extra)
        self.assertEqual(result["total"], 14)
        keys = {(p["a"]["key"], p["b"]["key"]) for p in result["repetition"]["pairs"]}
        self.assertTrue({("reports/1/960", "reports/1/961"), ("reports/1/961", "reports/1/960")} & keys)

    def test_all_period_is_not_capped_and_has_no_teacher_ranking(self):
        rows = []
        for offset in range(stats.MAX_COMPARE + 50):
            day = self.today - timedelta(days=offset)
            rows.append((f"reports/1/{offset}", report(day, 14, 0, f"{offset}번째 날"), f"{offset}번째 날", day))
        result = stats.analyze(rows, today=self.today, days=None)
        self.assertEqual(result["total"], stats.MAX_COMPARE + 50)
        self.assertEqual(result["timing"]["timed"], stats.MAX_COMPARE + 50)
        self.assertNotIn("authors", result)
        self.assertTrue(all("author" not in entry for entry in result["timing"]["flagged"]))

    def test_missing_upload_time(self):
        item = {"date_written": "2026-11-20", "content": "시간 정보 없음", "author": {"type": "teacher"}}
        result = stats.analyze([("reports/1/1", item, "시간 정보 없음", date(2026, 11, 20))],
                               today=self.today, days=90)
        self.assertEqual(result["timing"]["timed"], 0)
        self.assertIsNone(result["timing"]["baseline"])


class LifeAndPhotoTest(unittest.TestCase):
    def test_life_trend_flags_bad_week(self):
        today = date(2026, 11, 26)
        rows = []
        for offset in range(42):
            day = today - timedelta(days=offset)
            if day.weekday() >= 5:
                continue
            item = report(day, 14, 0, "기록")
            item["mood_status"] = "bad" if offset < 5 else "good"
            item["meal_status"] = "less" if offset < 3 else "fixed"
            rows.append((f"reports/1/{offset}", item, "기록", day))
        life = stats.analyze(rows, today=today, days=90)["life"]
        self.assertEqual(life["recorded"], len(rows))
        self.assertTrue(life["rising"])
        self.assertTrue(any("기분" in detail for detail in life["rising"][-1]["details"]))
        self.assertFalse(life["weeks"][0]["rising"])

    def test_photo_gaps(self):
        today = date(2026, 11, 26)          # Thursday
        days = [today - timedelta(days=n) for n in range(30, 60) if (today - timedelta(days=n)).weekday() < 5]
        days += [date(2026, 11, 2)]          # then one more photo, then nothing
        photos = stats.photo_gaps(days, today, None)
        self.assertEqual(photos["last"], "2026-11-02")
        self.assertEqual(photos["gaps"][0], {"after": "2026-11-02", "until": "", "weekdays": 18})
        self.assertEqual(stats.weekdays_between(date(2026, 11, 20), date(2026, 11, 24)), 1)


class StatsEndpointTest(unittest.TestCase):
    def test_stats_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            folder = root / "children" / "1" / "reports" / "5"
            folder.mkdir(parents=True)
            (root / "children" / "1" / "profile.json").write_text('{"name": "우하린"}', encoding="utf-8")
            today = date.today()
            (folder / "meta.json").write_text(json.dumps(report(today, 21, 0, "밤 알림장")), encoding="utf-8")
            client = create_app(root).test_client()
            kwargs = {"headers": {"X-Remote-User-Groups": "system-admin"},
                      "environ_overrides": {"REMOTE_ADDR": "172.30.32.2"}}
            data = client.get("/api/stats?days=30", **kwargs).json
            self.assertEqual(data["owner"], "1")
            self.assertEqual(data["owners"], [{"id": "1", "name": "우하린"}])
            self.assertEqual(data["total"], 1)
            self.assertEqual(data["timing"]["flagged"][0]["tags"], ["after"])
            self.assertEqual(client.get("/api/stats?days=7", **kwargs).status_code, 400)
            self.assertEqual(client.get("/api/stats?owner=x", **kwargs).status_code, 400)

            # cached result refreshes when a report changes
            (folder / "meta.json").write_text(json.dumps(report(today, 14, 0, "낮 알림장")), encoding="utf-8")
            os.utime(folder / "meta.json", ns=(1, 2))
            self.assertEqual(client.get("/api/stats?days=30", **kwargs).json["timing"]["flagged"], [])

            # 확인함 check
            res = client.post("/api/stats/review", json={"key": "reports/1/5", "reviewed": True}, **kwargs)
            self.assertEqual(res.json["reviewed"], ["reports/1/5"])
            self.assertEqual(client.get("/api/stats?days=30", **kwargs).json["reviewed"], ["reports/1/5"])
            self.assertEqual(client.post("/api/stats/review", json={"key": "../x", "reviewed": True},
                                         **kwargs).status_code, 400)
            res = client.post("/api/stats/review", json={"key": "reports/1/5", "reviewed": False}, **kwargs)
            self.assertEqual(res.json["reviewed"], [])

            # 하원 후 기록
            day = today.isoformat()
            res = client.post("/api/home-notes", json={"owner": "1", "date": day, "flags": ["mood", "bruise"],
                                                       "memo": " 팔에 멍 "}, **kwargs)
            self.assertEqual(res.json["notes"], [{"date": day, "flags": ["bruise", "mood"],
                                                  "labels": ["멍·상처", "기분 변화"], "memo": "팔에 멍"}])
            detail = client.get("/api/item?key=reports/1/5", **kwargs).json
            self.assertEqual(detail["home_note"]["memo"], "팔에 멍")
            self.assertEqual(client.get("/api/stats?days=30", **kwargs).json["notes"][0]["date"], day)
            for bad in ({"owner": "1", "date": "2026-02-30", "flags": []},
                        {"owner": "1", "date": day, "flags": ["nope"]},
                        {"owner": "1", "date": day, "memo": "가" * 1001}):
                self.assertEqual(client.post("/api/home-notes", json=bad, **kwargs).status_code, 400)
            res = client.post("/api/home-notes", json={"owner": "1", "date": day}, **kwargs)
            self.assertEqual(res.json["notes"], [])
            self.assertIsNone(client.get("/api/item?key=reports/1/5", **kwargs).json["home_note"])


if __name__ == "__main__":
    unittest.main()
