"""Tiny shared helper: post a message to a channel named by an env var,
searching every guild the bot is in. Used by feedback (bug/feature
reports), nl (ambiguous-ruling escalation), and rule_edit (edit log) -
each has its own channel, but the lookup is identical."""
import os
import sys

import discord


async def post(client: discord.Client, env_var: str, text: str) -> None:
    channel_name = os.environ.get(env_var)
    if not channel_name:
        print(f"WARN: {env_var} not set, dropping message meant for that channel", file=sys.stderr)
        return
    try:
        for guild in client.guilds:
            channel = discord.utils.get(guild.text_channels, name=channel_name)
            if channel:
                await channel.send(text)
                return
        print(f"WARN: channel '{channel_name}' ({env_var}) not found in any guild", file=sys.stderr)
    except Exception as exc:
        print(f"WARN: post to '{channel_name}' ({env_var}) failed: {exc!r}", file=sys.stderr)
