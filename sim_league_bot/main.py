"""Entrypoint: load secrets/config, wire up slash commands + the DM NL
handler, run the bot."""
import sys

import discord
from discord import app_commands

from . import commands, discordchan, feedback, nl, secrets, voice


def main() -> None:
    secrets.load()
    token = secrets.require("DISCORD_BOT_TOKEN")
    secrets.require("ANTHROPIC_API_KEY")

    intents = discord.Intents.default()
    intents.message_content = True
    bot = discord.Client(intents=intents)
    tree = app_commands.CommandTree(bot)
    commands.setup(tree)

    @bot.event
    async def on_ready():
        await tree.sync()
        print(f"INFO: logged in as {bot.user}", file=sys.stderr)

    @bot.event
    async def on_message(message: discord.Message):
        if message.author.bot:
            return
        try:
            await nl.handle(message, bot)
        except Exception as exc:
            print(f"ERROR: on_message failed: {exc!r}", file=sys.stderr)
            try:
                await message.channel.send(voice.BROKE)
            except Exception:
                pass
            auto = feedback.auto_report("on_message", repr(exc))
            if auto.notify:
                await discordchan.post(bot, "FEEDBACK_CHANNEL_NAME", auto.notify)

    @tree.error
    async def on_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        print(f"ERROR: slash command error: {error!r}", file=sys.stderr)
        text = voice.BROKE
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)
        command_name = interaction.command.qualified_name if interaction.command else "unknown"
        auto = feedback.auto_report(f"slash /{command_name}", repr(error))
        if auto.notify:
            await discordchan.post(interaction.client, "FEEDBACK_CHANNEL_NAME", auto.notify)

    bot.run(token)


if __name__ == "__main__":
    main()
