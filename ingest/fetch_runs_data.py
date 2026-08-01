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

            for attempt in range(5):
                try:
                    resp = client.get(EXPORT_URL, params=params)
                    if resp.status_code >= 500:  # server hiccup: retryable
                        raise httpx.TransportError(f"server returned {resp.status_code}")
                    break
                except httpx.TransportError as e:
                    if attempt == 4:
                        raise
                    wait = 2 ** attempt * 5  # 5s, 10s, 20s, 40s
                    print(f"transport error ({e}), retry {attempt + 1}/4 in {wait}s", flush=True)
                    time.sleep(wait)
            if resp.status_code == 429:
                wait = min(max(float(resp.headers.get("Retry-After", "10")), 1), 300)
                print(f"429 rate-limited, waiting {wait:.0f}s", flush=True)
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
                # X-RateLimit-Reset is a unix timestamp (float), not a duration
                reset = float(resp.headers.get("X-RateLimit-Reset", "60"))
                wait = reset - time.time() if reset > 1e9 else reset
                wait = min(max(wait, 1), 300)
                print(f"rate limit exhausted, waiting {wait:.0f}s", flush=True)
                time.sleep(wait)
