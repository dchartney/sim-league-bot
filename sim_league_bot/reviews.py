"""Rule review notes: admins flag a rule "for review" while reading the book,
with a note, and later list / resolve them. This never touches the rule text
(rules/*.md) - it is a separate bookkeeping file, review-notes.json
(gitignored, on CT 111). Every new flag / resolution is also mirrored to the
channel RULE_REVIEW_CHANNEL_NAME (default "rule-review") so the admin team
sees them as they come in.

Used from two places: the admin chat (model tools in nl.py) and the
/rule flag, /rule flags, /rule resolve slash commands. Admin checks happen
in those callers.
"""
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

from . import rules

STORE_PATH = Path(__file__).resolve().parent.parent / "review-notes.json"
os.environ.setdefault("RULE_REVIEW_CHANNEL_NAME", "rule-review")
_RULE_NO = re.compile(r"^\d+(\.\d+)*$")


def _load() -> dict:
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        data = {}
    data.setdefault("items", [])
    data.setdefault("next_id", 1)
    return data


def _save(data: dict) -> None:
    tmp = STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(STORE_PATH)


def normalize_rule(number: str) -> str:
    return (number or "").strip().strip(".").replace("Rule", "").replace("rule", "").strip()


def add(rule: str, note: str, by: str, by_id: str) -> tuple:
    """Returns (item or None, message). Refuses a malformed rule number; accepts a
    well-formed number that isn't in the book but says so."""
    rule = normalize_rule(rule)
    note = (note or "").strip()[:1000]
    if not _RULE_NO.match(rule):
        return None, f"'{rule}' doesn't look like a rule number (expected something like 3.4.2)."
    try:
        exists = rules.find_rule(rule) is not None
    except (ValueError, OSError):
        exists = False
    data = _load()
    item = {"id": data["next_id"], "rule": rule, "note": note, "by": by, "by_id": by_id,
            "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "status": "open", "rule_in_book": exists}
    data["next_id"] += 1
    data["items"].append(item)
    _save(data)
    extra = "" if exists else " (heads up: I couldn't find that exact rule number in the book, so check it)"
    return item, f"Flagged rule {rule} for review as #{item['id']}{extra}."


def list_items(include_resolved: bool = False) -> list:
    return [i for i in _load()["items"] if include_resolved or i["status"] == "open"]


def resolve(item_id: int, by: str, resolution: str = "") -> tuple:
    data = _load()
    for item in data["items"]:
        if item["id"] == item_id:
            if item["status"] != "open":
                return None, f"Flag #{item_id} is already resolved."
            item.update(status="resolved", resolved_by=by, resolved_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                        resolution=(resolution or "").strip()[:500])
            _save(data)
            return item, f"Marked flag #{item_id} (rule {item['rule']}) as resolved."
    return None, f"No review flag #{item_id}."


def format_list(items: list, limit: int = 25) -> str:
    if not items:
        return "No open review flags."
    lines = []
    for i in items[:limit]:
        status = "" if i["status"] == "open" else " [resolved]"
        lines.append(f"**#{i['id']}** rule {i['rule']}{status} - {i['note'] or '(no note)'} _({i['by']}, {i['created'][:10]})_")
    if len(items) > limit:
        lines.append(f"... and {len(items) - limit} more")
    return "\n".join(lines)[:1900]


def mirror_flagged(item: dict) -> str:
    note = item["note"] or "(no note)"
    return f"\U0001F6A9 **Rule {item['rule']} flagged for review** (#{item['id']}) by {item['by']}: {note}"[:1900]


def mirror_resolved(item: dict, by: str) -> str:
    res = f" - {item['resolution']}" if item.get("resolution") else ""
    return f"✅ **Review flag #{item['id']} (rule {item['rule']}) resolved** by {by}{res}"[:1900]
