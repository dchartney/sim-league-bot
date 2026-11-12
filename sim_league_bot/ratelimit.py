"""Per-user rate limit on the expensive path (full rulebook + Claude call),
so one person can't run up the API bill by hammering /ruling or DMing
rules questions. In-memory, resets on restart - fine for this purpose,
no need for persistence. Exact limit is a placeholder (David hasn't
settled on a number yet) - tune via RULING_RATE_LIMIT / RULING_RATE_WINDOW_SECONDS."""
import os
import time

from . import admins

_hits: dict = {}


def _limit() -> int:
    return int(os.environ.get("RULING_RATE_LIMIT", "6"))


def _window() -> int:
    return int(os.environ.get("RULING_RATE_WINDOW_SECONDS", str(60 * 60)))


def allow(user_id: str) -> bool:
    # League admins (admins.json) are exempt from the limit. The limit exists
    # to cap spend from the open server, and admins are a small trusted group.
    if admins.is_admin(user_id):
        return True
    now = time.time()
    window = _window()
    hits = [t for t in _hits.get(user_id, []) if now - t < window]
    if len(hits) >= _limit():
        _hits[user_id] = hits
        return False
    hits.append(now)
    _hits[user_id] = hits
    return True
