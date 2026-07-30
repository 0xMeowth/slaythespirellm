"""Stream run export pages from /api/exports/runs with cursor pagination and rate-limit backoff."""

import gzip
import time
from collections.abc import Iterator

import httpx

EXPORT_URL = "https://spire-codex.com/api/exports/runs"


def fetch_pages(
    page_size: int = 1000,
    start: str | None = None,
    cursor: str | None = None,
) -> Iterator[tuple[list[bytes], str | None]]:
    """Yield (lines, next_cursor) per page.

    next_cursor decodes to submitted_at|run_hash of the page's last run;
    passing it back resumes after that run.
    """
    with httpx.Client(timeout=120) as client:
        while True:
            params: dict[str, str | int] = {"limit": page_size}
            if cursor:
                params["cursor"] = cursor
            elif start:
                params["start"] = start

            resp = client.get(EXPORT_URL, params=params)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "10"))
                time.sleep(wait)
                continue
            resp.raise_for_status()

            lines = [l for l in gzip.decompress(resp.content).splitlines() if l]
            next_cursor = resp.headers.get("X-Next-Cursor")
            if not lines:
                return
            yield lines, next_cursor
            if not next_cursor:
                return
            cursor = next_cursor

            remaining = int(resp.headers.get("X-RateLimit-Remaining", "10"))
            if remaining <= 1:
                reset = int(resp.headers.get("X-RateLimit-Reset", "60"))
                time.sleep(max(reset, 1))
