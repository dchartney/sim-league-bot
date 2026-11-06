"""Admin roster: who the ambiguous-ruling escalation and rulebook-edit-log
channels are for, and who passes the role check on admin-only slash
commands. Backed by admins.json at the repo root (not gitignored - it's
not a secret, just names + Discord IDs once known).

TODO: discord_id is null for everyone until the Discord application and
the admin role exist; is_admin() can't do anything useful until then."""
import json
from pathlib import Path

ADMINS_PATH = Path(__file__).resolve().parent.parent / "admins.json"


def _load() -> list:
    try:
        return json.loads(ADMINS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []


def is_admin(discord_id: str) -> bool:
    return any(str(a.get("discord_id")) == str(discord_id) for a in _load() if a.get("discord_id"))


def admin_ids() -> list:
    return [str(a["discord_id"]) for a in _load() if a.get("discord_id")]


def name_for(discord_id: str) -> str:
    for a in _load():
        if str(a.get("discord_id")) == str(discord_id):
            return a["name"]
    return "an admin"
