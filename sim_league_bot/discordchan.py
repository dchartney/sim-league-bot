"""Tiny shared helper: post a message to a channel named by an env var,
searching every guild the bot is in. Used by feedback (bug/feature
reports), nl (ambiguous-ruling escalation, driver reports), and rule_edit
(edit log) - each has its own channel, but the lookup is identical.

Messages may contain the placeholder {{ADMIN_ROLE}}, which is replaced with a
real mention of the league admin role (ADMIN_ROLE_ID if set, else a
case-insensitive match on ADMIN_ROLE_NAME, default "League ADMINS"). If the
role can't be found the placeholder is dropped and the message still posts,
just without the ping. Only role mentions actually ping: user mentions in the
text (e.g. the reporter) render but do not notify."""
import os
import sys

import discord

ROLE_PLACEHOLDER = "{{ADMIN_ROLE}}"
DEFAULT_ADMIN_ROLE_NAME = "League ADMINS"


def _admin_role_mention(guild: discord.Guild) -> str:
    role_id = os.environ.get("ADMIN_ROLE_ID")
    role = None
    if role_id and role_id.isdigit():
        role = guild.get_role(int(role_id))
    if role is None:
        wanted = os.environ.get("ADMIN_ROLE_NAME", DEFAULT_ADMIN_ROLE_NAME).strip().lower()
        role = next((r for r in guild.roles if r.name.strip().lower() == wanted), None)
    if role is None:
        print(f"WARN: admin role not found in guild '{guild.name}' (ADMIN_ROLE_ID/ADMIN_ROLE_NAME), posting without a ping", file=sys.stderr)
        return ""
    return role.mention


def find_channel(client: discord.Client, env_var: str):
    """The text channel named by env var `env_var`, in any guild the bot is in, or None."""
    channel_name = os.environ.get(env_var)
    if not channel_name:
        return None
    for guild in client.guilds:
        channel = discord.utils.get(guild.text_channels, name=channel_name)
        if channel:
            return channel
    return None


async def post(client: discord.Client, env_var: str, text: str) -> bool:
    """Returns True if the message was posted, False if it was dropped."""
    channel_name = os.environ.get(env_var)
    if not channel_name:
        print(f"WARN: {env_var} not set, dropping message meant for that channel", file=sys.stderr)
        return False
    try:
        for guild in client.guilds:
            channel = discord.utils.get(guild.text_channels, name=channel_name)
            if channel:
                if ROLE_PLACEHOLDER in text:
                    text = text.replace(ROLE_PLACEHOLDER, _admin_role_mention(guild)).strip()
                await channel.send(
                    text,
                    allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
                )
                return True
        print(f"WARN: channel '{channel_name}' ({env_var}) not found in any guild", file=sys.stderr)
    except Exception as exc:
        print(f"WARN: post to '{channel_name}' ({env_var}) failed: {exc!r}", file=sys.stderr)
    return False
