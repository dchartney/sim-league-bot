"""Bug reports and feature requests about the bot itself. Open to anyone
(not family-only, unlike household-manager's /feedback) so these post to
a channel instead of DMing an admin - see FEEDBACK_CHANNEL_NAME. Logged to
feedback.json (gitignored) either way, so a recurring issue can be found
later even if the channel message scrolled off."""
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

import discord

from . import discordchan

FEEDBACK_PATH = Path(__file__).resolve().parent.parent / "feedback.json"
_lock = threading.Lock()


class FeedbackResult(str):
    """Confirmation text, plus `notify` (the channel message to post) when one is due."""

    def __new__(cls, text: str, notify: Optional[str] = None):
        obj = super().__new__(cls, text)
        obj.notify = notify
        return obj


def _load() -> dict:
    try:
        return json.loads(FEEDBACK_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {"items": [], "next_id": 1}


def _save(data: dict) -> None:
    FEEDBACK_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _submit(kind: str, description: str, reporter_name: str, reporter_id: str) -> FeedbackResult:
    description = " ".join(description.split())
    label = "bug report" if kind == "bug" else "feature request"
    if not description:
        return FeedbackResult(f"What's the {label}? Give me a sentence or two.")
    with _lock:
        data = _load()
        item = {
            "id": data["next_id"], "type": kind, "reporter": reporter_name, "reporter_id": reporter_id,
            "description": description, "created": datetime.now().isoformat(timespec="seconds"),
            "status": "open",
        }
        data["items"].append(item)
        data["next_id"] += 1
        _save(data)
    notify = f"**{'Bug report' if kind == 'bug' else 'Feature request'} #{item['id']}** from {reporter_name}: {description}"
    return FeedbackResult(f"Got it - {label} #{item['id']} is logged.", notify)


def report_bug(description: str, reporter_name: str, reporter_id: str) -> FeedbackResult:
    return _submit("bug", description, reporter_name, reporter_id)


def request_feature(description: str, reporter_name: str, reporter_id: str) -> FeedbackResult:
    return _submit("feature", description, reporter_name, reporter_id)


def auto_report(context: str, detail: str) -> FeedbackResult:
    """Ricky files a report on himself when something breaks - never shown
    to the user directly, only `.notify` matters; deduped against an
    already-open identical report so a recurring outage doesn't spam the
    channel on every single message."""
    description = f"[auto] {context}: {detail}".strip()
    description = " ".join(description.split())[:500]
    with _lock:
        data = _load()
        if any(i["status"] == "open" and i.get("description") == description for i in data["items"]):
            return FeedbackResult("")
        item = {
            "id": data["next_id"], "type": "bug", "reporter": "(auto)", "reporter_id": "",
            "description": description, "created": datetime.now().isoformat(timespec="seconds"),
            "status": "open",
        }
        data["items"].append(item)
        data["next_id"] += 1
        _save(data)
    notify = f"**Bug report #{item['id']}** (auto-filed) - {description}"
    return FeedbackResult("", notify)


async def post_to_channel(client: discord.Client, text: str) -> None:
    await discordchan.post(client, "FEEDBACK_CHANNEL_NAME", text)
