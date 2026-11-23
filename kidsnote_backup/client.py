"""Read-only Kidsnote API and attachment client."""
from __future__ import annotations

import random
from urllib.parse import urlencode, urljoin, urlsplit

import requests
from kidsnote_auth import DEFAULT_USER_AGENT, KIDSNOTE_BASE
from pacing import RequestPacer, TRANSIENT_DELAYS


class BackupError(RuntimeError):
    """An error with a message safe for the app log."""


class HttpError(BackupError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"HTTP {status}")


class TransportError(BackupError):
    """Network retries were exhausted."""


def checked_https(url: str):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme == "https" and parsed.hostname and parsed.username is None
                and parsed.password is None and parsed.port in (None, 443)):
            return parsed
    except ValueError:
        pass
    raise BackupError("Kidsnote returned an invalid HTTPS URL.")


def first_party(url: str) -> bool:
    return checked_https(url).hostname == urlsplit(KIDSNOTE_BASE).hostname


def next_page(initial: str, current: str, value: str | int) -> str:
    if isinstance(value, str):
        if value.startswith(("https://", "http://", "/", "?")):
            return urljoin(current, value)
    elif type(value) is not int or value < 1:
        raise BackupError("Kidsnote returned an unexpected pagination token.")
    # Kidsnote's bare continuation is passed as page, including opaque strings
    # from reports, albums and notices and integer page numbers from other lists.
    return initial + ("&" if "?" in initial else "?") + urlencode({"page": value})


class KidsnoteClient:
    def __init__(self, pacer: RequestPacer | None = None):
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": DEFAULT_USER_AGENT, "Accept-Language": "ko"})
        self.pacer = pacer if pacer is not None else RequestPacer()
        self.token = ""
        self.last_count: int | None = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.session.close()

    def _request(self, url, headers, media):
        for delay in (*TRANSIENT_DELAYS, None):
            self.session.cookies.clear()
            self.pacer.wait(media=media)
            try:
                response = self.session.get(
                    url, headers=headers, stream=media, allow_redirects=False,
                    timeout=(10, 60 if media else 30),
                )
            except requests.RequestException as exc:
                if delay is None:
                    raise TransportError("Could not reach Kidsnote or its media server.") from exc
            else:
                if response.status_code in (429, 503):
                    with response:
                        self.pacer.check_response(response.status_code, response.headers.get("Retry-After"))
                if response.status_code not in (500, 502, 504) or delay is None:
                    return response
                response.close()
            self.pacer.pause(delay + random.uniform(0, 3))

    def get(self, url: str, *, media: bool = False):
        for _ in range(6):
            trusted = first_party(url)
            if not media and not trusted:
                raise BackupError("Kidsnote API returned a link outside its own origin.")
            # Send the session ID only to the exact Kidsnote hostname, never to a CDN.
            headers = {"Cookie": f"sessionid={self.token}"} if trusted and self.token else {}
            headers["Accept"] = "*/*" if media else "application/json"
            if media:
                headers["Accept-Encoding"] = "identity"
            response = self._request(url, headers, media)
            if response.status_code == 200:
                return response
            with response:
                if media and response.is_redirect:
                    url = urljoin(url, response.headers["Location"])
                    continue
                raise HttpError(response.status_code)
        raise BackupError("Too many attachment redirects.")

    def json(self, url: str) -> dict:
        with self.get(url) as response:
            try:
                payload = response.json()
            except ValueError as exc:
                raise BackupError("Kidsnote returned an unreadable JSON response.") from exc
        if not isinstance(payload, dict):
            raise BackupError("Kidsnote returned an unexpected JSON response.")
        return payload

    def items(self, path: str, *, alternate: str = "", **params):
        initial = KIDSNOTE_BASE + path + "?" + urlencode({"page_size": 100, **params})
        url = initial
        seen_pages = set()
        seen_ids = set()
        self.last_count = None
        while url:
            if url in seen_pages or len(seen_pages) >= 2000:
                raise BackupError("Kidsnote pagination did not terminate.")
            seen_pages.add(url)
            payload = self.json(url)
            records = payload.get("results", payload.get(alternate))
            if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
                raise BackupError("Kidsnote returned an unexpected list response.")
            if len(seen_pages) == 1:
                count = payload.get("count")
                has_next = bool(payload.get("next"))
                if (type(count) is int and count >= len(records)
                        and (not has_next or count > len(records))):
                    self.last_count = count
            for item in records:
                item_id = item.get("id")
                if item_id is not None:
                    key = identifier(item_id)
                    if key in seen_ids:
                        continue
                    seen_ids.add(key)
                yield item
            following = payload.get("next")
            if following is None or following == "":
                return
            url = next_page(initial, url, following)


def identifier(value) -> str:
    value = str(value)
    if not value.isascii() or not value.isdecimal() or len(value) > 30:
        raise BackupError("Kidsnote returned an invalid record identifier.")
    return value
