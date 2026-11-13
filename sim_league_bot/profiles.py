"""New-driver profile interviews.

When someone joins the SCSRA Discord (or an admin runs /profile start), Ricky
DMs them and runs a short, conversational interview: racing background, home
track, favourite car, a proud/painful moment, why they joined, a fun fact.
When it's done the bot posts the questions asked, the driver's answers and a
DRAFT promo tweet to the admin-category channel PROFILE_CHANNEL_NAME (default
"new-driver-profiles") for Devin to review and adjust. Nothing is posted publicly
and nothing is tweeted - it's for driver/server engagement.

The driver can say "skip" at any point (or just ignore the DM; sessions expire
after 7 days). State lives in profiles.json (gitignored) so a bot restart
mid-interview doesn't lose the conversation.

Cost bounds: the cheap model, no rule book in context, a hard cap on driver
replies per interview, and one interview per member.
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import discord
from anthropic import Anthropic

from . import discordchan, voice

PROFILE_MODEL = "claude-haiku-4-5"
STATE_PATH = Path(__file__).resolve().parent.parent / "profiles.json"
SESSION_TTL_SECONDS = 7 * 24 * 3600
MAX_DRIVER_REPLIES = 20
os.environ.setdefault("PROFILE_CHANNEL_NAME", "new-driver-profiles")

SYSTEM = (
    f"{voice.PERSONA}\n\n"
    "RIGHT NOW your job is different: you are welcoming a NEW DRIVER to the SCSRA Discord and interviewing them "
    "in DMs so the league can build a driver profile. The admin team will use the answers to write a fun "
    "promo tweet welcoming them. Keep the swagger friendly and welcoming - never mean, never about rules or "
    "penalties here.\n"
    "How to run it:\n"
    "- Your first message: greet them by name, say you're Ricky Bobby with SCSRA Rules & Operations, explain in "
    "one or two sentences that you're collecting a few quick answers for a driver profile the admins may use for "
    "a welcome post, that they can say 'skip' any time, and ask the first question.\n"
    "- Ask ONE or TWO questions at a time, conversationally, reacting briefly to what they say. Cover roughly "
    "these topics (skip any that don't fit, follow up if an answer is interesting): how long they've sim "
    "raced / how they got into it; real-life or iRacing background and favourite car or series; home or "
    "favourite track; racing hero; proudest (or most painful) moment on track; why they joined SCSRA; one fun "
    "fact about them off track. Aim for about 5-7 questions total.\n"
    "- Never ask for personal contact info, real address, age, employer, or anything private. If they volunteer "
    "something sensitive, leave it out of the profile.\n"
    "- When you have enough (or they say they're done), call finish_profile with every question you asked and "
    "their answers (tidied, in their words, nothing invented) and a DRAFT tweet (max 270 characters, upbeat, "
    "welcoming, uses only facts they gave, may include #SCSRA). Also write a short friendly closing message "
    "telling them the admins will see it.\n"
    "- If they say skip / no thanks / not interested, call skip_profile and reply with a short, gracious line.\n"
    "- Plain text only, keep every message short (under 600 characters)."
)

TOOLS = [
    {
        "name": "finish_profile",
        "description": "Call when the interview is complete. Posts the Q&A and a draft tweet to the admin team.",
        "input_schema": {
            "type": "object",
            "properties": {
                "qa": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"question": {"type": "string"}, "answer": {"type": "string"}},
                        "required": ["question", "answer"],
                    },
                },
                "tweet": {"type": "string", "description": "Draft welcome tweet, max 270 characters"},
            },
            "required": ["qa", "tweet"],
        },
    },
    {
        "name": "skip_profile",
        "description": "Call when the driver declines or asks to skip the interview.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

_anthropic: Optional[Anthropic] = None


def _client() -> Anthropic:
    global _anthropic
    if _anthropic is None:
        _anthropic = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _anthropic


# ---- persistence -----------------------------------------------------------

def _load() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        data = {}
    data.setdefault("sessions", {})
    data.setdefault("done", {})
    return data


def _save(data: dict) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _live_session(data: dict, user_id: str) -> Optional[dict]:
    session = data["sessions"].get(user_id)
    if session and time.time() - session["last"] > SESSION_TTL_SECONDS:
        del data["sessions"][user_id]
        _save(data)
        return None
    return session


# ---- model call ------------------------------------------------------------

async def _turn(session: dict):
    return await asyncio.to_thread(
        _client().messages.create,
        model=PROFILE_MODEL,
        max_tokens=1024,
        system=SYSTEM + f"\n\nThe driver's Discord display name is: {session['name']}.",
        tools=TOOLS,
        messages=session["messages"],
    )


def _split(response):
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    tool = next((b for b in response.content if b.type == "tool_use"), None)
    return text, tool


def _format_post(user_id: str, name: str, qa: list, tweet: str) -> list:
    lines = [f"**New driver profile: {name}** (<@{user_id}>)", ""]
    for item in qa[:12]:
        lines.append(f"**Q:** {str(item.get('question', ''))[:300]}")
        lines.append(f"**A:** {str(item.get('answer', ''))[:600]}")
        lines.append("")
    lines.append("**Draft tweet** (Devin: adjust as you like, nothing has been posted anywhere):")
    lines.append("> " + tweet.strip()[:280].replace("\n", "\n> "))
    text = "\n".join(lines)
    # Discord's 2000-char limit: split on line boundaries.
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > 1900:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current)
    return chunks


# ---- public API ------------------------------------------------------------

async def start(member: discord.abc.User, client: discord.Client, force: bool = False) -> str:
    """Begin an interview by DM. Returns a short status string (used by /profile start)."""
    if member.bot:
        return "That's a bot."
    user_id = str(member.id)
    data = _load()
    if force:
        data["sessions"].pop(user_id, None)
        data["done"].pop(user_id, None)
    elif _live_session(data, user_id) or user_id in data["done"]:
        return "Already interviewed (or one is in progress)."

    name = getattr(member, "display_name", None) or member.name
    session = {
        "name": name,
        "started": time.time(),
        "last": time.time(),
        "replies": 0,
        "messages": [{"role": "user", "content": "[A new driver just joined the server. Send your opening message.]"}],
    }
    try:
        response = await _turn(session)
    except Exception as exc:
        print(f"WARN: profile opener failed for {user_id}: {exc!r}", file=sys.stderr)
        return "Couldn't generate the opening message."
    text, _ = _split(response)
    if not text:
        return "Model returned no opening message."
    try:
        await member.send(text)
    except discord.Forbidden:
        print(f"INFO: can't DM new member {user_id} (DMs closed), no profile interview", file=sys.stderr)
        return "Their DMs are closed, so I couldn't message them."
    session["messages"].append({"role": "assistant", "content": text})
    data["sessions"][user_id] = session
    _save(data)
    print(f"INFO: profile interview started for {user_id}", file=sys.stderr)
    return f"Interview started with {name}."


async def handle_reply(message: discord.Message, client: discord.Client) -> bool:
    """If this DM is part of a live profile interview, handle it and return True."""
    user_id = str(message.author.id)
    data = _load()
    session = _live_session(data, user_id)
    if session is None:
        return False
    text = message.content.strip()
    if not text:
        return True  # attachment-only message mid-interview: ignore quietly

    session["replies"] += 1
    session["last"] = time.time()
    content = text
    if session["replies"] >= MAX_DRIVER_REPLIES:
        content += "\n\n[System: interview length limit reached - call finish_profile now with what you have.]"
    session["messages"].append({"role": "user", "content": content})
    print(f"INFO: profile interview reply from {user_id} ({len(text)} chars, #{session['replies']})", file=sys.stderr)

    try:
        response = await _turn(session)
    except Exception as exc:
        print(f"WARN: profile turn failed for {user_id}: {exc!r}", file=sys.stderr)
        session["messages"].pop()
        session["replies"] -= 1
        _save(data)
        await message.channel.send(voice.BROKE)
        return True

    reply, tool = _split(response)
    if tool is not None and tool.name == "finish_profile":
        qa = tool.input.get("qa") or []
        tweet = tool.input.get("tweet") or ""
        for chunk in _format_post(user_id, session["name"], qa, tweet):
            await discordchan.post(client, "PROFILE_CHANNEL_NAME", chunk)
        data["sessions"].pop(user_id, None)
        data["done"][user_id] = {"name": session["name"], "finished": time.time(), "qa": qa, "tweet": tweet}
        _save(data)
        await message.channel.send(reply or "That's a wrap - thanks! The admins will take a look at your profile.")
        return True
    if tool is not None and tool.name == "skip_profile":
        data["sessions"].pop(user_id, None)
        data["done"][user_id] = {"name": session["name"], "skipped": True, "finished": time.time()}
        _save(data)
        await message.channel.send(reply or "No sweat, friend. If you change your mind, just holler.")
        return True

    if not reply:
        reply = "Go on, I'm listening."
    session["messages"].append({"role": "assistant", "content": reply})
    _save(data)
    await message.channel.send(reply[:1900])
    return True
