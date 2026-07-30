"""Seed cards/relics reference tables from /api/exports/eng. Run: uv run python -m ingest.reference"""

import io
import json
import re
import zipfile

import httpx

from ingest.db import connect

EXPORT_URL = "https://spire-codex.com/api/exports/eng"
TAG_RE = re.compile(r"\[/?([a-z_]+)(?::(\d+))?\]", re.IGNORECASE)


def strip_markup(text: str | None) -> str | None:
    if text is None:
        return None
    # icon tags carry meaning: [energy:2] -> "2 Energy"; color tags just wrap text
    return TAG_RE.sub(
        lambda m: f"{m.group(2)} {m.group(1).capitalize()}" if m.group(2) else "",
        text,
    )


def main() -> None:
    resp = httpx.get(EXPORT_URL, timeout=60)
    resp.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(resp.content))

    cards = json.loads(zf.read("cards.json"))
    relics = json.loads(zf.read("relics.json"))

    conn = connect()
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO cards VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    c["id"],
                    c["name"],
                    strip_markup(c.get("description")),
                    c.get("cost"),
                    c.get("is_x_cost"),
                    c.get("type"),
                    c.get("rarity"),
                    c.get("color"),
                    c.get("target"),
                    ",".join(c["keywords"]) if c.get("keywords") else None,
                )
                for c in cards
            ],
        )
        conn.executemany(
            "INSERT OR REPLACE INTO relics VALUES (?,?,?,?,?)",
            [
                (
                    r["id"],
                    r["name"],
                    strip_markup(r.get("description")),
                    r.get("rarity"),
                    r.get("pool"),
                )
                for r in relics
            ],
        )

    n_cards = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    n_relics = conn.execute("SELECT COUNT(*) FROM relics").fetchone()[0]
    print(f"cards: {n_cards}, relics: {n_relics}")


if __name__ == "__main__":
    main()
