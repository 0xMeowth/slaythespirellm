"""Parse one raw export line into rows for runs, run_cards, run_relics, run_card_choices."""

import hashlib
import json
from typing import Any


def bare(entity_id: str | None) -> str | None:
    """CARD.STRIKE_SILENT -> STRIKE_SILENT; NONE.NONE -> None."""
    if not entity_id or entity_id == "NONE.NONE":
        return None
    return entity_id.split(".", 1)[-1]


def parse_line(raw: bytes, submitted_at: str | None) -> dict[str, Any]:
    d = json.loads(raw)
    run_id = hashlib.sha256(raw).hexdigest()
    player = d["players"][0]
    history = d.get("map_point_history") or []

    run = (
        run_id,
        bare(player["character"]),
        int(bool(d["win"])),
        int(bool(d["was_abandoned"])),
        d["ascension"],
        d.get("game_mode"),
        d.get("seed"),
        d.get("build_id"),
        d.get("run_time"),
        d.get("start_time"),
        submitted_at,
        sum(len(act) for act in history),
        ",".join(filter(None, (bare(a) for a in d.get("acts") or []))) or None,
        bare(d.get("killed_by_encounter")),
        bare(d.get("killed_by_event")),
        d.get("platform_type"),
        d.get("schema_version"),
        int(bool(d.get("is_beta"))),
    )

    cards = [
        (run_id, bare(c["id"]), c.get("current_upgrade_level", 0), c.get("floor_added_to_deck"))
        for c in player.get("deck") or []
    ]
    relics = [
        (run_id, bare(r["id"]), r.get("floor_added_to_deck"))
        for r in player.get("relics") or []
    ]

    choices = []
    for act_no, act in enumerate(history, start=1):
        for floor_no, point in enumerate(act, start=1):
            for stats in point.get("player_stats") or []:
                for ch in stats.get("card_choices") or []:
                    card_id = bare(ch.get("card", {}).get("id"))
                    if card_id:
                        choices.append(
                            (run_id, act_no, floor_no, card_id, int(bool(ch.get("was_picked"))))
                        )

    return {"run": run, "cards": cards, "relics": relics, "choices": choices}
