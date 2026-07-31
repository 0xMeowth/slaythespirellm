"""Sync runs from /api/exports/runs into SQLite.

First pull:  uv run python -m ingest.sync_runs_data --start 2026-06-01 --max-runs 1000
Cron/resume: uv run python -m ingest.sync_runs_data   (continues from saved cursor)
"""

import argparse
import base64
import gzip
from datetime import datetime, timezone

from ingest.db import connect
from ingest.fetch_runs_data import fetch_pages
from ingest.parse_runs_data import parse_line


def cursor_timestamp(cursor: str | None) -> str | None:
    """Cursor decodes to 'submitted_at|run_hash'. Approximates submitted_at for the page's runs."""
    if not cursor:
        return None
    try:
        return base64.b64decode(cursor).decode().split("|")[0]
    except Exception:
        return None


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", help="ISO date lower bound for a fresh pull, e.g. 2026-06-01")
    ap.add_argument("--max-runs", type=int, help="stop after roughly this many runs")
    ap.add_argument("--page-size", type=int, default=1000)
    args = ap.parse_args()

    conn = connect()
    row = conn.execute("SELECT value FROM sync_state WHERE key='last_cursor'").fetchone()
    cursor = row[0] if row else None

    log_id = conn.execute(
        "INSERT INTO sync_log (started_at, status) VALUES (?, 'running')", (now(),)
    ).lastrowid
    conn.commit()

    total = 0
    status, error = "failed", None
    try:
        for lines, next_cursor in fetch_pages(args.page_size, args.start, cursor):
            submitted_at = cursor_timestamp(next_cursor)
            with conn:
                for raw in lines:
                    rows = parse_line(raw, submitted_at)
                    inserted = conn.execute(
                        "INSERT OR IGNORE INTO runs VALUES (" + ",".join("?" * 18) + ")",
                        rows["run"],
                    ).rowcount
                    if not inserted:  # already have this run; don't duplicate children
                        continue
                    run_id = rows["run"][0]
                    conn.executemany("INSERT INTO run_cards VALUES (?,?,?,?)", rows["cards"])
                    conn.executemany("INSERT INTO run_relics VALUES (?,?,?)", rows["relics"])
                    conn.executemany(
                        "INSERT INTO run_card_choices VALUES (?,?,?,?,?)", rows["choices"]
                    )
                    conn.execute(
                        "INSERT OR IGNORE INTO raw_runs VALUES (?,?)",
                        (run_id, gzip.compress(raw)),
                    )
                    total += 1
                if next_cursor:
                    conn.execute(
                        "INSERT OR REPLACE INTO sync_state VALUES ('last_cursor', ?)",
                        (next_cursor,),
                    )
            print(f"{now()}  +{len(lines)} fetched, {total} new")
            if args.max_runs and total >= args.max_runs:
                break
        status = "ok"
    except Exception as e:
        error = repr(e)
        raise
    finally:
        conn.execute(
            "UPDATE sync_log SET finished_at=?, runs_fetched=?, status=?, error=? WHERE id=?",
            (now(), total, status, error, log_id),
        )
        conn.commit()
        conn.close()

    print(f"done: {total} new runs")


if __name__ == "__main__":
    main()
