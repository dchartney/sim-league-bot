"""Car numbers already taken for the season.

Source of truth = the Discord channel SIGNUP_CHANNEL_NAME (default
"season-13-signup-list"), where each signed-up driver is listed with a number
and a name. Ricky reads it live (message history) every time a number is
needed, and writes a snapshot to numbers.md (gitignored) as a cache/history.
If the channel can't be read (permissions, outage) the last snapshot is used;
if there's no snapshot either, refresh() returns None and callers must NOT
offer numbers (better to say "ask an admin" than to offer a taken number).

Parsing is deliberately tolerant: per line, the first standalone 1-3 digit
number is the car number and the rest of the line (minus punctuation) is the
name, e.g. "42 John Smith", "#42 - John Smith", "**42** John Smith",
"John Smith #42" all work. Lines with no number, or dates like 1/6, are
skipped. /numbers shows what was parsed so odd formats can be spotted.
"""
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import discord

CACHE_PATH = Path(__file__).resolve().parent.parent / "numbers.md"
os.environ.setdefault("SIGNUP_CHANNEL_NAME", "season-13-signup-list")
HISTORY_LIMIT = 1000

_NUM = re.compile(r"(?<![\d/.:\w])#?(\d{1,3})(?![\d/:\w]|\.\d)")
_HEADER = re.compile(r"\b(season|sign\s?-?ups?|list|roster|registered|drivers?)\b", re.I)
_STRIP = re.compile(r"[\*_`~|>\-–—:#,]+")


def number_max() -> int:
    try:
        # Capped at 599: the two-step dropdown (25-number ranges, max 25 ranges) can't go higher.
        return max(1, min(599, int(os.environ.get("NUMBER_MAX", "99"))))
    except ValueError:
        return 99


def parse_line(line: str) -> Optional[tuple]:
    """(number, name) or None."""
    line = line.strip()
    if not line:
        return None
    if _HEADER.search(line):
        return None  # headings like "Season 13 Signups" are not entries
    match = _NUM.search(line)
    if not match:
        return None
    number = int(match.group(1))
    rest = (line[:match.start()] + " " + line[match.end():])
    rest = re.sub(r"<[@#&!]+\d+>", "", rest)  # Discord mentions
    name = re.sub(r"\s+", " ", _STRIP.sub(" ", rest)).strip()
    return number, name


async def _read_channel(client: discord.Client):
    name = os.environ["SIGNUP_CHANNEL_NAME"]
    for guild in client.guilds:
        channel = discord.utils.get(guild.text_channels, name=name)
        if channel is None:
            continue
        taken, skipped = {}, []
        async for msg in channel.history(limit=HISTORY_LIMIT):
            if msg.author.bot and not msg.content:
                continue
            for line in msg.content.splitlines():
                parsed = parse_line(line)
                if parsed:
                    taken.setdefault(parsed[0], parsed[1])
                elif line.strip():
                    skipped.append(line.strip()[:80])
        return taken, skipped
    raise LookupError(f"channel '{name}' not found")


def _read_cache() -> Optional[dict]:
    try:
        text = CACHE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    taken = {}
    for row in re.finditer(r"^\|\s*(\d{1,3})\s*\|\s*(.*?)\s*\|\s*$", text, re.M):
        taken[int(row.group(1))] = row.group(2)
    return taken or None


def _write_cache(taken: dict, extra_reserved: dict) -> None:
    lines = [
        f"# Season car numbers (snapshot of #{os.environ['SIGNUP_CHANNEL_NAME']}, {time.strftime('%Y-%m-%d %H:%M')})",
        "",
        "| # | Driver |",
        "|---|---|",
    ]
    for number in sorted(taken):
        lines.append(f"| {number} | {taken[number]} |")
    if extra_reserved:
        lines += ["", "Reserved via Ricky interviews (not yet on the signup list):"]
        lines += [f"- #{n}: {who}" for n, who in sorted(extra_reserved.items())]
    try:
        CACHE_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"WARN: couldn't write numbers.md: {exc!r}", file=sys.stderr)


async def refresh(client: discord.Client, extra_reserved: Optional[dict] = None) -> Optional[tuple]:
    """Returns (taken {number: name}, skipped_lines, source) or None if
    neither the channel nor a cached snapshot is available. source is
    'channel' or 'cache'."""
    try:
        taken, skipped = await _read_channel(client)
        if taken:
            _write_cache(taken, extra_reserved or {})
            return taken, skipped, "channel"
        print("WARN: signup channel read OK but no numbers parsed; trying cache", file=sys.stderr)
    except Exception as exc:
        print(f"WARN: couldn't read signup channel: {exc!r}; trying cache", file=sys.stderr)
    cached = _read_cache()
    if cached:
        return cached, [], "cache"
    return None


def free_numbers(taken: dict, reserved: dict, user_id: str) -> list:
    """Numbers 1..NUMBER_MAX (0 is the pace car) not taken and not reserved by someone else."""
    blocked = set(taken) | {int(n) for n, uid in reserved.items() if uid != user_id}
    return [n for n in range(1, number_max() + 1) if n not in blocked]
