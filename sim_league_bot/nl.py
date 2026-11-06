"""Natural-language front door: DMs only (no public ask channel - see the
design notes). Two Claude calls, not one, because this bot is open to
anyone in the Discord server rather than family-only like household-manager:

  1. A cheap classification call (Haiku, no rule book in context) decides
     whether the message is even in scope (schedule/rules) before paying
     for anything expensive. Off-topic messages stop here - no big call,
     no cost. Only `handle()` (the DM path, where intent is unknown) uses
     this; the /ruling and /schedule slash commands already know their
     own intent and skip straight to `ask()`.
  2. Only if in scope, AND the user hasn't hit the per-user rate limit,
     the real call goes out with the whole rule book in context (prompt
     caching - the rule book is the cacheable prefix, reused across many
     different questions). Claude answers directly for a clear ruling or
     a schedule question; for a genuinely ambiguous ruling it calls
     escalate_ruling instead of guessing.

The system prompt scoping alone is NOT the cost control - without the
classifier gate in front of it, anyone could still force an expensive
call just by asking anyway. The classifier is what actually caps spend.
"""
import asyncio
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

import discord
from anthropic import Anthropic

from . import discordchan, feedback, ratelimit, rules, voice

CLASSIFY_MODEL = "claude-haiku-4-5"
MAIN_MODEL = "claude-opus-4-5"

TOOLS = [
    {
        "name": "escalate_ruling",
        "description": (
            "Use this INSTEAD of answering when the rule book does not clearly cover the driver's scenario - "
            "never guess or invent a ruling the book doesn't support. Posts the question to the admin team."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scenario": {"type": "string", "description": "The driver's scenario, summarized"},
                "reason": {"type": "string", "description": "Why the rule book doesn't clearly cover it"},
            },
            "required": ["scenario", "reason"],
        },
    },
    {
        "name": "report_bug",
        "description": "Report a bug/problem with the bot itself. Use when the user says something is broken or wrong.",
        "input_schema": {"type": "object", "properties": {"description": {"type": "string"}}, "required": ["description"]},
    },
    {
        "name": "request_feature",
        "description": "Suggest a new feature or improvement for the bot. Use when the user offers an idea or asks for something it can't do.",
        "input_schema": {"type": "object", "properties": {"description": {"type": "string"}}, "required": ["description"]},
    },
]

_anthropic: Optional[Anthropic] = None


def _client() -> Anthropic:
    global _anthropic
    if _anthropic is None:
        _anthropic = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    return _anthropic


async def classify_in_scope(text: str) -> bool:
    """True if in scope (schedule or rules question), False otherwise.
    Fails open to "in scope" on an API error - rate limiting downstream
    still bounds the damage, and refusing a legitimate question because
    the classifier itself broke is a worse failure mode than one extra
    expensive call."""
    try:
        response = await asyncio.to_thread(
            _client().messages.create,
            model=CLASSIFY_MODEL,
            max_tokens=10,
            system=(
                "Reply with exactly one word: SCOPE or OTHER. SCOPE if the message is a question about the "
                "SCSRA iRacing league's race schedule, or a rules/ruling question about a racing scenario. "
                "OTHER for anything else (chitchat, unrelated topics, testing the bot, etc)."
            ),
            messages=[{"role": "user", "content": text}],
        )
        answer = "".join(b.text for b in response.content if b.type == "text").strip().upper()
        return "SCOPE" in answer
    except Exception as exc:
        print(f"WARN: classify call failed, failing open to in-scope: {exc}", file=sys.stderr)
        return True


@dataclass
class AskResult:
    reply: str
    notifications: list = field(default_factory=list)  # [(text, channel_env_var)]


def _dispatch_tool(name: str, tool_input: dict, reporter_name: str, reporter_id: str) -> tuple:
    """Returns (result_text_or_None, notify_text_or_None, notify_channel_env_var_or_None)."""
    if name == "escalate_ruling":
        scenario = tool_input.get("scenario", "")
        reason = tool_input.get("reason", "")
        notify = f"**Ruling needs a call** - {reporter_name} asked: {scenario}\nWhy it's not clear-cut: {reason}"
        return voice.escalation_line(), notify, "ESCALATION_CHANNEL_NAME"
    if name == "report_bug":
        result = feedback.report_bug(tool_input.get("description", ""), reporter_name, reporter_id)
        return str(result), result.notify, "FEEDBACK_CHANNEL_NAME"
    if name == "request_feature":
        result = feedback.request_feature(tool_input.get("description", ""), reporter_name, reporter_id)
        return str(result), result.notify, "FEEDBACK_CHANNEL_NAME"
    return None, None, None


async def ask(text: str, reporter_name: str, reporter_id: str) -> AskResult:
    """The expensive call: whole rule book in context (cached), Ricky's
    persona, tools for escalation/feedback. Caller is responsible for the
    cost gate (classify_in_scope for free-text DMs) and the rate limit
    (ratelimit.allow) - this function always makes the call."""
    system = (
        f"{voice.PERSONA} "
        "Today's rule book follows. Cite exact rule numbers (e.g. 'Rule 3.4.2') when answering a rules "
        "question, and quote the relevant line(s). If the rule book does not clearly cover the driver's "
        "scenario, call escalate_ruling instead of answering - never guess, soften, or invent a ruling "
        "the book doesn't support. For a schedule question: the schedule is not finalized yet this season, "
        "so say that plainly rather than guessing at dates.\n\n"
        f"RULE BOOK:\n{rules.load_rulebook_text()}"
    )

    try:
        response = await asyncio.to_thread(
            _client().messages.create,
            model=MAIN_MODEL,
            max_tokens=1024,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS,
            messages=[{"role": "user", "content": text}],
        )
    except Exception as exc:
        print(f"WARN: main NL call failed: {exc}", file=sys.stderr)
        auto = feedback.auto_report("NL main call", repr(exc))
        notifications = [(auto.notify, "FEEDBACK_CHANNEL_NAME")] if auto.notify else []
        return AskResult(voice.BROKE, notifications)

    draft_reply = None
    notifications = []
    for block in response.content:
        if block.type == "text":
            draft_reply = block.text
        elif block.type == "tool_use":
            result, notify, channel_var = _dispatch_tool(block.name, block.input, reporter_name, reporter_id)
            if result:
                draft_reply = result
            if notify and channel_var:
                notifications.append((notify, channel_var))

    return AskResult(draft_reply or "Not sure what to tell you on that one.", notifications)


async def send_notifications(client: discord.Client, result: AskResult) -> None:
    for notify, channel_var in result.notifications:
        await discordchan.post(client, channel_var, notify)


async def handle(message: discord.Message, client: discord.Client) -> None:
    if not isinstance(message.channel, discord.DMChannel):
        return  # DMs only - no public ask channel
    if message.author.bot:
        return

    text = message.content.strip()
    if not text:
        return

    user_id = str(message.author.id)
    if not await classify_in_scope(text):
        await message.channel.send(voice.OFF_TOPIC)
        return

    if not ratelimit.allow(user_id):
        await message.channel.send(voice.RATE_LIMITED)
        return

    result = await ask(text, message.author.display_name, user_id)
    await message.channel.send(result.reply)
    await send_notifications(client, result)
