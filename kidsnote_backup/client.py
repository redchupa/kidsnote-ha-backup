"""Read-only Kidsnote API and attachment client."""
from __future__ import annotations

from urllib.parse import urlencode, urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from kidsnote_auth import DEFAULT_USER_AGENT, KIDSNOTE_BASE


class BackupError(RuntimeError):
    """An error with a message safe for the app log."""


class HttpError(BackupError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"HTTP {status}")


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


def next_page(initial: str, current: str, value: str) -> str:
    if value.startswith(("https://", "http://", "/", "?")):
        return urljoin(current, value)
    return initial + ("&" if "?" in initial else "?") + urlencode({"cursor": value})


class KidsnoteClient:
    def __init__(self):
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": DEFAULT_USER_AGENT, "Accept-Language": "ko"})
        retry = Retry(
            total=2, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods={"GET"}, backoff_max=10, retry_after_max=30,
            raise_on_status=False,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.token = ""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.session.close()

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
            self.session.cookies.clear()
            try:
                response = self.session.get(
                    url, headers=headers, stream=media, allow_redirects=False,
                    timeout=(10, 60 if media else 30),
                )
            except (requests.RequestException, ValueError) as exc:
                raise BackupError("Connection to Kidsnote or its media server failed.") from exc
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
        while url:
            if url in seen_pages or len(seen_pages) >= 2000:
                raise BackupError("Kidsnote pagination did not terminate.")
            seen_pages.add(url)
            payload = self.json(url)
            records = payload.get("results", payload.get(alternate))
            if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
                raise BackupError("Kidsnote returned an unexpected list response.")
            for item in records:
                item_id = item.get("id")
                if item_id is not None:
                    key = identifier(item_id)
                    if key in seen_ids:
                        continue
                    seen_ids.add(key)
                yield item
            following = payload.get("next")
            if following and not isinstance(following, str):
                raise BackupError("Kidsnote returned an unexpected pagination token.")
            url = next_page(initial, url, following) if following else ""


def identifier(value) -> str:
    value = str(value)
    if not value.isascii() or not value.isdecimal() or len(value) > 30:
        raise BackupError("Kidsnote returned an invalid record identifier.")
    return value
