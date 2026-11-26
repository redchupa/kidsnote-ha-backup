"""통계분석: pattern indicators over backed-up teacher reports.

Nothing here decides that something bad happened. It only points at reports a
parent may want to look at again: ones uploaded at an unusual hour, during
extended/integrated care, long after the day they describe, or whose
sentences repeat other reports word for word.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from supplies import KST, TEACHER_TYPES

# Daycare time bands in minutes from midnight (KST).
BANDS = (
    ("before", "보육 전 (07:30 이전)", 0, 450),
    ("morning", "등원 시간 (07:30–09:00)", 450, 540),
    ("core", "기본보육 (09:00–16:00)", 540, 960),
    ("extended", "연장·통합보육 (16:00–19:30)", 960, 1170),
    ("after", "보육시간 외 (19:30 이후)", 1170, 1440),
)
CHECK_BANDS = {"extended", "after", "before"}
MIN_BASELINE = 8          # reports needed before "unusual for this teacher" is meaningful
MIN_SHIFT_MINUTES = 90    # never call less than 1h30 off the usual time unusual
LATE_DAYS = 2             # uploaded this many days after the date it describes
MIN_SENTENCE = 8          # normalized characters; shorter lines are greetings
NEAR_DUPLICATE = 0.7
MAX_REPORTS = 400
MAX_LIST = 60

_SPLIT = re.compile(r"(?<=[.!?~♡♥])\s+|\n+")
_NOISE = re.compile(r"[\W_]+", re.UNICODE)


def uploaded_at(item) -> datetime | None:
    """When Kidsnote's server recorded the report (KST), if a time is known."""
    for key in ("created", "date_created", "modified"):
        value = item.get(key)
        if not isinstance(value, str) or len(value) <= 10:
            continue
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=KST)
        return moment.astimezone(KST)
    return None


def band_of(minute: int) -> str:
    for band, _, start, end in BANDS:
        if start <= minute < end:
            return band
    return "after"


def clock(minute: float) -> str:
    minute = int(round(minute)) % 1440
    return f"{minute // 60:02d}:{minute % 60:02d}"


def sentences(text: str):
    for line in _SPLIT.split(text or ""):
        line = " ".join(line.split())
        if line:
            yield line


def normalize(sentence: str, names=()) -> str:
    for name in names:
        sentence = sentence.replace(name, "○")
    return _NOISE.sub("", sentence).casefold()


def shingles(text: str) -> set[str]:
    return {text[i:i + 3] for i in range(max(len(text) - 2, 0))}


def name_variants(name: str):
    """``우하린`` → (``우하린``, ``하린``) so ``하린이가`` and ``하린이는`` normalize alike."""
    name = (name or "").strip()
    variants = [name] if len(name) >= 2 else []
    if len(name) == 3:
        variants.append(name[1:])
    return tuple(variants)


def analyze(reports, child_name="", today: date | None = None, days: int | None = 90, photo_days=()):
    """``reports``: iterable of (key, meta dict, plain text body, written date).

    ``photo_days``: dates on which an album or report carried at least one photo.
    """
    names = name_variants(child_name)
    oldest = (today - timedelta(days=days)) if (today and days) else None
    rows = []
    for key, item, text, written in reports:
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        if author and str(author.get("type", "")).lower() not in TEACHER_TYPES:
            continue
        if oldest and written and written < oldest:
            continue
        rows.append({
            "key": key, "item": item, "text": text, "date": written,
            "uploaded": uploaded_at(item),
            "author": " ".join(str(author.get("name") or item.get("author_name") or "").split()),
            "title": " ".join(str(item.get("title") or "").split()) or (text.splitlines() or [""])[0][:60],
        })
    rows.sort(key=lambda row: (row["date"] or date.min, row["key"]), reverse=True)
    rows = rows[:MAX_REPORTS]
    return {
        "total": len(rows),
        "timing": timing(rows),
        "repetition": repetition(rows, names),
        "authors": authors(rows),
        "life": life_trend(rows),
        "photos": photo_gaps(photo_days, today or date.today(), oldest),
    }


def timing(rows):
    timed = [row for row in rows if row["uploaded"]]
    minutes = [row["uploaded"].hour * 60 + row["uploaded"].minute for row in timed]
    hist = [0] * 24
    for minute in minutes:
        hist[minute // 60] += 1
    band_counts = Counter(band_of(minute) for minute in minutes)
    baseline = None
    if len(minutes) >= MIN_BASELINE:
        median = statistics.median(minutes)
        mad = statistics.median(abs(m - median) for m in minutes)
        baseline = {"median": clock(median), "median_minute": median,
                    "spread": round(mad), "limit": max(MIN_SHIFT_MINUTES, 3 * mad), "n": len(minutes)}
    flagged = []
    for row, minute in zip(timed, minutes):
        reasons, tags = [], []
        band = band_of(minute)
        if baseline and abs(minute - baseline["median_minute"]) > baseline["limit"]:
            shift = minute - baseline["median_minute"]
            hours = abs(shift) / 60
            reasons.append(f"평소({baseline['median']} 전후)보다 {hours:.1f}시간 {'늦게' if shift > 0 else '일찍'} 업로드")
            tags.append("shift")
        if band in CHECK_BANDS:
            reasons.append(dict((b, label) for b, label, _, _ in BANDS)[band] + "에 업로드")
            tags.append(band)
        if row["date"]:
            gap = (row["uploaded"].date() - row["date"]).days
            if gap >= LATE_DAYS:
                reasons.append(f"알림장 날짜보다 {gap}일 늦게 업로드 (몰아쓰기 가능성)")
                tags.append("late")
        if reasons:
            flagged.append({
                "key": row["key"], "date": row["date"].isoformat() if row["date"] else "",
                "uploaded": row["uploaded"].strftime("%Y-%m-%d %H:%M"), "title": row["title"],
                "author": row["author"], "reasons": reasons, "tags": tags,
            })
    return {
        "timed": len(timed),
        "untimed": len(rows) - len(timed),
        "hist": hist,
        "bands": [{"id": b, "label": label, "count": band_counts.get(b, 0), "check": b in CHECK_BANDS}
                  for b, label, _, _ in BANDS],
        "baseline": {k: v for k, v in baseline.items() if k != "median_minute"} if baseline else None,
        "flagged": flagged[:MAX_LIST],
        "flagged_total": len(flagged),
    }


def repetition(rows, names):
    seen = defaultdict(list)          # normalized sentence -> [(row index, original)]
    per_row = []
    for index, row in enumerate(rows):
        kept = []
        for line in sentences(row["text"]):
            norm = normalize(line, names)
            if len(norm) >= MIN_SENTENCE:
                kept.append(norm)
                if not seen[norm] or seen[norm][-1][0] != index:
                    seen[norm].append((index, line))
        per_row.append(kept)

    repeated = {norm for norm, hits in seen.items() if len(hits) >= 2}
    top = sorted(
        ((norm, hits) for norm, hits in seen.items() if norm in repeated),
        key=lambda pair: (-len(pair[1]), pair[1][0][0]),
    )
    top_sentences = [{
        "text": hits[0][1], "count": len(hits),
        "reports": [{"key": rows[i]["key"], "date": rows[i]["date"].isoformat() if rows[i]["date"] else ""}
                    for i, _ in hits[:8]],
    } for norm, hits in top[:30]]

    ratios = []
    for index, kept in enumerate(per_row):
        if kept:
            share = sum(norm in repeated for norm in kept) / len(kept)
            ratios.append((share, index, len(kept)))
    average = statistics.mean(share for share, _, _ in ratios) if ratios else 0.0

    bodies = [shingles(normalize(row["text"], names)) for row in rows]
    pairs = []
    for i in range(len(rows)):
        if len(bodies[i]) < 20:
            continue
        for j in range(i + 1, len(rows)):
            if len(bodies[j]) < 20:
                continue
            union = len(bodies[i] | bodies[j])
            score = len(bodies[i] & bodies[j]) / union if union else 0
            if score >= NEAR_DUPLICATE:
                pairs.append((score, i, j))
    pairs.sort(reverse=True)

    lengths = [len(row["text"]) for row in rows if row["text"]]
    sentence_lengths = [len(line) for row in rows for line in sentences(row["text"])]

    def cv(values):
        if len(values) < 5:
            return None
        mean = statistics.mean(values)
        return round(statistics.pstdev(values) / mean, 2) if mean else None

    if not ratios:
        verdict = "분석할 문장이 부족합니다."
    elif average >= 0.5:
        verdict = "같은 문장을 반복해 쓰는 비율이 높습니다. 템플릿·복사 작성이 많은 편입니다."
    elif average >= 0.2:
        verdict = "일부 문장이 반복됩니다. 인사말·공통 안내 문구일 수 있습니다."
    else:
        verdict = "대부분 매번 다른 문장으로 작성되었습니다."

    return {
        "average_ratio": round(average, 3),
        "verdict": verdict,
        "length_cv": cv(lengths),
        "sentence_cv": cv(sentence_lengths),
        "sentences": top_sentences,
        "reports": [{
            "key": rows[i]["key"], "date": rows[i]["date"].isoformat() if rows[i]["date"] else "",
            "title": rows[i]["title"], "ratio": round(share, 2), "sentences": count,
        } for share, i, count in sorted(ratios, reverse=True)[:20] if share > 0],
        "pairs": [{
            "score": round(score, 2),
            "a": {"key": rows[i]["key"], "date": rows[i]["date"].isoformat() if rows[i]["date"] else "", "title": rows[i]["title"]},
            "b": {"key": rows[j]["key"], "date": rows[j]["date"].isoformat() if rows[j]["date"] else "", "title": rows[j]["title"]},
        } for score, i, j in pairs[:20]],
        "pairs_total": len(pairs),
    }


# Life-record values that mean "not a good day" for that category.
LIFE_WATCH = {
    "mood_status": ("기분", {"bad"}),
    "health_status": ("건강", {"bad", "sick"}),
    "meal_status": ("식사", {"less", "none", "bad"}),
    "sleep_hour": ("낮잠", {"no_sleep", "sleep_hardly", "slp_bad", "under_30m"}),
    "bowel_status": ("배변", {"diarrhea", "watery"}),
    "temperature_status": ("체온", {"high", "slight"}),
}
LIFE_MIN_WEEKS = 4      # weeks of history before a week can stand out
PHOTO_MIN_GAP = 5       # weekdays without any photo before a gap is shown


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def life_trend(rows):
    """Weekly count of "not a good day" life records, flagging weeks well above usual."""
    weeks = defaultdict(lambda: {"reports": 0, **{field: 0 for field in LIFE_WATCH}})
    for row in rows:
        if not row["date"]:
            continue
        week = weeks[week_start(row["date"])]
        week["reports"] += 1
        for field, (_, bad) in LIFE_WATCH.items():
            if str(row["item"].get(field, "")) in bad:
                week[field] += 1
    ordered = sorted(weeks.items())
    totals = [sum(entry[field] for field in LIFE_WATCH) for _, entry in ordered]
    usual = statistics.mean(totals) if totals else 0
    result = []
    for (start, entry), total in zip(ordered, totals):
        details = [f"{LIFE_WATCH[field][0]} {entry[field]}회" for field in LIFE_WATCH if entry[field]]
        rising = len(ordered) >= LIFE_MIN_WEEKS and total >= 2 and total >= 2 * max(usual, 0.5)
        result.append({"week": start.isoformat(), "reports": entry["reports"], "total": total,
                       "details": details, "rising": rising})
    return {
        "labels": [label for label, _ in LIFE_WATCH.values()],
        "usual": round(usual, 1),
        "weeks": result[-26:],
        "rising": [week for week in result if week["rising"]][-10:],
        "recorded": sum(1 for row in rows if any(row["item"].get(field) for field in LIFE_WATCH)),
    }


def weekdays_between(first: date, last: date) -> int:
    """Weekdays strictly between two dates."""
    count, day = 0, first + timedelta(days=1)
    while day < last:
        count += day.weekday() < 5
        day += timedelta(days=1)
    return count


def photo_gaps(photo_days, today: date, oldest: date | None):
    """Stretches of weekdays with no photo in albums or reports."""
    days = sorted({day for day in photo_days if (not oldest or day >= oldest) and day <= today})
    if not days:
        return {"days": 0, "gaps": [], "usual": None, "last": ""}
    spans = [(days[i], days[i + 1]) for i in range(len(days) - 1)] + [(days[-1], today + timedelta(days=1))]
    gaps = [(weekdays_between(a, b), a, b) for a, b in spans]
    usual = statistics.median(gap for gap, _, _ in gaps[:-1]) if len(gaps) > 1 else 0
    limit = max(PHOTO_MIN_GAP, 3 * usual)
    shown = [{
        "after": a.isoformat(),
        "until": b.isoformat() if b <= today else "",
        "weekdays": gap,
    } for gap, a, b in gaps if gap >= limit]
    return {"days": len(days), "gaps": shown[::-1][:20], "usual": usual, "last": days[-1].isoformat()}


def authors(rows):
    counts = Counter(row["author"] or "이름 없음" for row in rows)
    return [{"name": name, "count": count} for name, count in counts.most_common()]
