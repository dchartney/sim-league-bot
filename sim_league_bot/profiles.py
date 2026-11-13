"""New-driver profile interviews.

When someone joins the SCSRA Discord (or an admin runs /new-member-test),
Ricky DMs them and runs a short, conversational interview. Required, in order:
how they heard about the SCSRA; iRacing name + iRating; first/last name; car
number (typed by the driver, checked live against the signup list - see
numbers.py); then a few fun questions. When it's done the bot posts the
answers, a DRAFT promo tweet and a PROPOSED server nickname
("First Last #Number") to the admin-category channel PROFILE_CHANNEL_NAME
(default "new-driver-profiles"). An admin presses Confirm to apply the
nickname (or Reject to release the number). Nothing is tweeted or posted
publicly.

The driver can say "skip" at any point (or ignore the DM; sessions expire
after 7 days). State lives in profiles.json (gitignored) so a restart
mid-interview doesn't lose the conversation. The nickname Confirm/Reject
buttons are persistent (discord.py DynamicItem), so they survive restarts.

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

from . import admins, discordchan, numbers, voice

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
    "a welcome post, that they can say 'skip' any time, and ask the FIRST required question.\n"
    "- REQUIRED questions, in this order: (1) How did they hear about the SCSRA? (in your opening message) "
    "(2) Their iRacing name and current iRating (ask together). (3) Their real first and last name, as they "
    "want it shown in the server. (4) Their car number: ask what number they would like (they type it, 0 to 99). When they give one, call claim_number with it. The bot tells you if it is free or taken; if taken, offer the suggested alternatives and let them pick again. Once you have (1)-(3) ask for the number in your next message. Make sure all of these appear in "
    "the finished profile exactly as given.\n"
    "- Then ask ONE or TWO questions at a time, conversationally, reacting briefly to what they say. Cover roughly "
    "these topics (skip any that don't fit, follow up if an answer is interesting): how long they've sim "
    "raced / how they got into it; real-life or iRacing background and favourite car or series; home or "
    "favourite track; racing hero; proudest (or most painful) moment on track; why they joined SCSRA; one fun "
    "fact about them off track. Aim for about 5-7 questions after the required ones.\n"
    "- Never ask for personal contact info, real address, age, employer, or anything private. If they volunteer "
    "something sensitive, leave it out of the profile.\n"
    "- When you have enough (or they say they're done), call finish_profile with every question you asked and "
    "their answers (tidied, in their words, nothing invented), the required fields, and a DRAFT tweet (max 270 "
    "characters, upbeat, welcoming, uses only facts they gave, may include #SCSRA). Also write a short friendly "
    "closing message telling them the admins will see it and will confirm their number and server nickname. "
    "Do NOT call finish_profile before the car number has been picked (unless the system says number lookup is "
    "unavailable).\n"
    "- If they say skip / no thanks / not interested, call skip_profile and reply with a short, gracious line.\n"
    "- Plain text only, keep every message short (under 600 characters)."
)

TOOLS = [
    {
        "name": "claim_number",
        "description": "Check and reserve the car number the driver typed. Call it as soon as they give a number "
                       "(after you have asked for their first choice). The bot replies with whether it is free.",
        "input_schema": {"type": "object", "properties": {"number": {"type": "integer"}}, "required": ["number"]},
    },
    {
        "name": "finish_profile",
        "description": "Call when the interview is complete. Posts the profile and a draft tweet to the admin team.",
        "input_schema": {
            "type": "object",
            "properties": {
                "how_heard": {"type": "string", "description": "How they heard about the SCSRA"},
                "iracing_name": {"type": "string"},
                "irating": {"type": "string", "description": "Current iRating as they gave it"},
                "full_name": {"type": "string", "description": "Real first and last name for the server nickname, e.g. 'John Smith'"},
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
    data.setdefault("reserved", {})  # "42" -> user_id
    return data


def _save(data: dict) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _release(data: dict, user_id: str) -> None:
    data["reserved"] = {n: u for n, u in data["reserved"].items() if u != user_id}


def _live_session(data: dict, user_id: str) -> Optional[dict]:
    session = data["sessions"].get(user_id)
    if session and time.time() - session["last"] > SESSION_TTL_SECONDS:
        del data["sessions"][user_id]
        _release(data, user_id)
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


# ---- car number claim ------------------------------------------------------

async def _claim_number(client: discord.Client, data: dict, user_id: str, session: dict, number) -> str:
    """Try to reserve the typed number. Returns a [System: ...] note for the model."""
    try:
        number = int(number)
    except (TypeError, ValueError):
        return "[System: that wasn't a valid number. Ask them for a whole number again.]"
    mx = numbers.number_max()
    if number < 0 or number > mx:
        return f"[System: car numbers here run 0 to {mx}. Ask them to choose another number in that range.]"
    info = await numbers.refresh(client, {int(n): u for n, u in data["reserved"].items()})
    if info is None:
        await discordchan.post(
            client, "FEEDBACK_CHANNEL_NAME",
            f"Couldn't check car numbers for {session['name']} (signup list unreadable, no saved copy). "
            f"Check Ricky's access to #{os.environ.get('SIGNUP_CHANNEL_NAME')}.")
        return ("[System: number lookup is unavailable right now. Tell the driver an admin will sort out their car "
                "number, then continue the interview and finish without a number.]")
    taken = info[0]
    holder = data["reserved"].get(str(number))
    if number in taken or (holder and holder != user_id):
        free = numbers.free_numbers(taken, data["reserved"], user_id)
        near = sorted(free, key=lambda n: (abs(n - number), n))[:6]
        who = f" (it belongs to {taken[number]})" if taken.get(number) else ""
        return (f"[System: #{number} is already taken{who}. Tell the driver, and suggest a few free ones nearby: "
                f"{', '.join('#' + str(n) for n in sorted(near))}. Let them type another number.]")
    _release(data, user_id)
    data["reserved"][str(number)] = user_id
    session["number"] = number
    _save(data)
    return (f"[System: car number #{number} is free and now reserved for the driver (pending admin confirmation). "
            "Acknowledge it briefly in your own voice and continue with the remaining questions.]")


# ---- posting + nickname confirmation ---------------------------------------

def _format_post(user_id: str, session: dict, fields: dict, qa: list, tweet: str, nick: Optional[str]) -> list:
    lines = [f"**New driver profile: {session['name']}** (<@{user_id}>)"]
    if fields.get("iracing_name") or fields.get("irating"):
        lines.append(f"**iRacing:** {fields.get('iracing_name', '?')} | iRating {fields.get('irating', '?')}")
    if fields.get("how_heard"):
        lines.append(f"**Heard about SCSRA via:** {str(fields['how_heard'])[:300]}")
    if session.get("number") is not None:
        lines.append(f"**Car number:** #{session['number']} (reserved, pending your confirmation)")
    if nick:
        warn = f" - **{len(nick)} chars, Discord's limit is 32, shorten it by hand**" if len(nick) > 32 else ""
        lines.append(f"**Proposed server nickname:** `{nick}`{warn}")
    lines.append("")
    for item in qa[:12]:
        lines.append(f"**Q:** {str(item.get('question', ''))[:300]}")
        lines.append(f"**A:** {str(item.get('answer', ''))[:600]}")
        lines.append("")
    lines.append("**Draft tweet** (Devin: adjust as you like, nothing has been posted anywhere):")
    lines.append("> " + tweet.strip()[:280].replace("\n", "\n> "))
    chunks, current = [], ""
    for line in "\n".join(lines).split("\n"):
        if len(current) + len(line) + 1 > 1900:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current)
    return chunks


class NickButton(discord.ui.DynamicItem[discord.ui.Button], template=r"scsra-nick:(?P<action>ok|no):(?P<uid>\d+)"):
    """Persistent Confirm/Reject buttons on a posted profile (survive restarts)."""

    def __init__(self, action: str, uid: str):
        confirm = action == "ok"
        super().__init__(discord.ui.Button(
            label="Confirm nickname + number" if confirm else "Reject",
            style=discord.ButtonStyle.success if confirm else discord.ButtonStyle.danger,
            custom_id=f"scsra-nick:{action}:{uid}"))
        self.action = action
        self.uid = uid

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["action"], match["uid"])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not admins.is_admin(str(interaction.user.id)):
            await interaction.response.send_message("Only an admin can do that.", ephemeral=True)
            return False
        return True

    async def callback(self, interaction: discord.Interaction) -> None:
        data = _load()
        entry = data["done"].get(self.uid)
        if not entry or not entry.get("nick") or entry.get("resolved"):
            await interaction.response.send_message("Nothing pending for this profile (already handled?).", ephemeral=True)
            return
        who = interaction.user.display_name
        if self.action == "no":
            _release(data, self.uid)
            entry["resolved"] = "rejected"
            _save(data)
            await interaction.response.edit_message(view=None)
            await interaction.followup.send(
                f"Rejected by {who}. Car #{entry.get('number')} released. `/new-member-test` can redo the interview.")
            return
        try:
            member = await interaction.guild.fetch_member(int(self.uid))
            await member.edit(nick=entry["nick"], reason=f"SCSRA signup confirmed by {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message(
                f"I can't change that nickname (I need the Manage Nicknames permission and a role above theirs; "
                f"server owners can't be changed at all). Set it by hand: `{entry['nick']}`", ephemeral=True)
            return
        except Exception as exc:
            await interaction.response.send_message(f"Nickname change failed: {exc}. Set it by hand: `{entry['nick']}`", ephemeral=True)
            return
        entry["resolved"] = "confirmed"
        _save(data)
        await interaction.response.edit_message(view=None)
        await interaction.followup.send(
            f"Confirmed by {who}: nickname set to **{entry['nick']}**, car #{entry.get('number')} is theirs. "
            f"(Add them to #{os.environ.get('SIGNUP_CHANNEL_NAME')} so the number stays taken.)")


async def _post_profile(client: discord.Client, user_id: str, session: dict, fields: dict, qa: list, tweet: str, nick: Optional[str]) -> bool:
    channel = discordchan.find_channel(client, "PROFILE_CHANNEL_NAME")
    if channel is None:
        print(f"WARN: profile channel '{os.environ['PROFILE_CHANNEL_NAME']}' not found", file=sys.stderr)
        return False
    chunks = _format_post(user_id, session, fields, qa, tweet, nick)
    try:
        for i, chunk in enumerate(chunks):
            view = None
            if nick and i == len(chunks) - 1:
                view = discord.ui.View(timeout=None)
                view.add_item(NickButton("ok", user_id))
                view.add_item(NickButton("no", user_id))
            await channel.send(chunk, view=view, allowed_mentions=discord.AllowedMentions.none())
        return True
    except Exception as exc:
        print(f"WARN: posting profile failed: {exc!r}", file=sys.stderr)
        return False


# ---- turn handling ---------------------------------------------------------

async def _run_turn(client: discord.Client, data: dict, user_id: str, session: dict, channel, _depth: int = 0) -> None:
    try:
        response = await _turn(session)
    except Exception as exc:
        print(f"WARN: profile turn failed for {user_id}: {exc!r}", file=sys.stderr)
        session["messages"].pop()
        session["replies"] = max(0, session["replies"] - 1)
        _save(data)
        await channel.send(voice.BROKE)
        return

    reply, tool = _split(response)

    if tool is not None and tool.name == "finish_profile":
        qa = tool.input.get("qa") or []
        tweet = tool.input.get("tweet") or ""
        fields = {k: tool.input.get(k) for k in ("how_heard", "iracing_name", "irating", "full_name")}
        number = session.get("number")
        full_name = (fields.get("full_name") or "").strip()
        nick = f"{full_name} #{number}" if full_name and number is not None else None
        posted = await _post_profile(client, user_id, session, fields, qa, tweet, nick)
        data["sessions"].pop(user_id, None)
        data["done"][user_id] = {"name": session["name"], "finished": time.time(), "qa": qa, "tweet": tweet,
                                 "posted": posted, "number": number, "nick": nick, "guild_id": session.get("guild_id"),
                                 **{k: v for k, v in fields.items() if v}}
        _save(data)
        if not posted:
            await discordchan.post(
                client, "FEEDBACK_CHANNEL_NAME",
                f"**Driver profile for {session['name']} could NOT be posted** to #{os.environ['PROFILE_CHANNEL_NAME']} "
                "(channel missing, or I lack View/Send permission there). The answers are saved on CT 111 in profiles.json.")
        await channel.send(reply or "That's a wrap - thanks! The admins will take a look at your profile.")
        return

    if tool is not None and tool.name == "skip_profile":
        data["sessions"].pop(user_id, None)
        _release(data, user_id)
        data["done"][user_id] = {"name": session["name"], "skipped": True, "finished": time.time()}
        _save(data)
        await channel.send(reply or "No sweat, friend. If you change your mind, just holler.")
        return

    if tool is not None and tool.name == "claim_number":
        number = tool.input.get("number")
        session["messages"].append({"role": "assistant", "content": reply or f"Let me check on #{number}."})
        if reply:
            await channel.send(reply[:1900])
        note = await _claim_number(client, data, user_id, session, number)
        session["messages"].append({"role": "user", "content": note})
        _save(data)
        if _depth < 3:
            await _run_turn(client, data, user_id, session, channel, _depth + 1)
        return

    if not reply:
        reply = "Go on, I'm listening."
    session["messages"].append({"role": "assistant", "content": reply})
    _save(data)
    await channel.send(reply[:1900])


# ---- public API ------------------------------------------------------------

async def start(member: discord.abc.User, client: discord.Client, force: bool = False) -> str:
    """Begin an interview by DM. Returns a short status string (used by /new-member-test)."""
    if member.bot:
        return "That's a bot."
    user_id = str(member.id)
    data = _load()
    if force:
        data["sessions"].pop(user_id, None)
        data["done"].pop(user_id, None)
        _release(data, user_id)
    elif _live_session(data, user_id) or user_id in data["done"]:
        return "Already interviewed (or one is in progress)."

    name = getattr(member, "display_name", None) or member.name
    guild = getattr(member, "guild", None)
    session = {
        "name": name,
        "guild_id": guild.id if guild else None,
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
    await _run_turn(client, data, user_id, session, message.channel)
    return True
