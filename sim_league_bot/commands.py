"""Slash commands. /ruling and /schedule reuse nl.ask() directly (same
rate limit, same expensive call) but skip the classify step - the user's
intent is already explicit from picking the command, so there's nothing
to classify."""
import asyncio
import os
import sys
from typing import Optional

import discord
from discord import app_commands

from . import admins, discordchan, feedback, nl, numbers, profiles, ratelimit, reviews, rule_edit, rules, voice, results


def _caller_id(interaction: discord.Interaction) -> str:
    return str(interaction.user.id)


async def _run_text(interaction: discord.Interaction, fn, ephemeral: bool = False) -> None:
    """Shared ack-then-work-then-reply wrapper, same reasoning as
    household-manager's commands._run: acknowledge immediately since
    Discord drops the interaction after 3s and a scrape/API call can be
    slower than that, run blocking work off the event loop, always reply."""
    await interaction.response.defer(ephemeral=ephemeral)
    try:
        text = await asyncio.to_thread(fn)
    except Exception as exc:
        print(f"ERROR: command failed: {exc!r}", file=sys.stderr)
        text = voice.BROKE
        command_name = interaction.command.qualified_name if interaction.command else "unknown"
        auto = feedback.auto_report(f"command /{command_name}", repr(exc))
        if auto.notify:
            await discordchan.post(interaction.client, "FEEDBACK_CHANNEL_NAME", auto.notify)
    await interaction.followup.send(text, ephemeral=ephemeral)


def setup(tree: app_commands.CommandTree) -> None:
    rule_group = app_commands.Group(name="rule", description="Rule book")
    results_group = app_commands.Group(name="results", description="Race results and standings")
    feedback_group = app_commands.Group(name="feedback", description="Report a bug or suggest a feature")

    @tree.command(name="ruling", description="Ask Ricky how a rule applies to a scenario")
    @app_commands.describe(scenario="Describe what happened")
    async def ruling_cmd(interaction: discord.Interaction, scenario: str):
        caller = _caller_id(interaction)
        if not ratelimit.allow(caller):
            await interaction.response.send_message(voice.RATE_LIMITED, ephemeral=True)
            return
        await interaction.response.defer()
        result = await nl.ask(scenario, interaction.user.display_name, caller)
        await interaction.followup.send(result.reply)
        await nl.send_notifications(interaction.client, result)

    @tree.command(name="schedule", description="Ask about the race schedule")
    @app_commands.describe(question="e.g. when's the next race")
    async def schedule_cmd(interaction: discord.Interaction, question: Optional[str] = None):
        caller = _caller_id(interaction)
        if not ratelimit.allow(caller):
            await interaction.response.send_message(voice.RATE_LIMITED, ephemeral=True)
            return
        await interaction.response.defer()
        result = await nl.ask(question or "What's the next race?", interaction.user.display_name, caller)
        await interaction.followup.send(result.reply)
        await nl.send_notifications(interaction.client, result)

    @rule_group.command(name="edit", description="(Admin) Edit a single numbered rule, e.g. 3.4.2")
    @app_commands.describe(number="The rule number to edit, e.g. 3.4.2")
    async def rule_edit_cmd(interaction: discord.Interaction, number: str):
        await rule_edit.start_edit(interaction, number.strip())

    @rule_group.command(name="show", description="Show the current text of a numbered rule")
    @app_commands.describe(number="The rule number, e.g. 3.4.2")
    async def rule_show_cmd(interaction: discord.Interaction, number: str):
        def work() -> str:
            block = rules.find_rule(number.strip())
            return block.text if block else f"No rule {number} found."
        await _run_text(interaction, work, ephemeral=True)

    @rule_group.command(name="diff", description="(Admin) Show what's changed in the rule book since a git tag (e.g. season-10)")
    @app_commands.describe(since="A git tag or commit to diff against, e.g. season-10")
    async def rule_diff_cmd(interaction: discord.Interaction, since: str):
        if not admins.is_admin(_caller_id(interaction)):
            await interaction.response.send_message("Only an admin can do that.", ephemeral=True)
            return
        def work() -> str:
            diff = rules.diff_since(since.strip())
            return f"```diff\n{diff[:1900]}\n```" if diff else f"No rule changes since {since}."
        await _run_text(interaction, work, ephemeral=True)

    def _admin_only(interaction: discord.Interaction) -> bool:
        return admins.is_admin(_caller_id(interaction))

    @rule_group.command(name="flag", description="(Admin) Flag a rule for review with a note")
    @app_commands.describe(number="The rule number, e.g. 3.4.2", note="Why it needs review")
    async def rule_flag_cmd(interaction: discord.Interaction, number: str, note: str):
        if not _admin_only(interaction):
            await interaction.response.send_message("Only an admin can do that.", ephemeral=True)
            return
        item, msg = reviews.add(number, note, interaction.user.display_name, _caller_id(interaction))
        await interaction.response.send_message(msg, ephemeral=True)
        if item:
            await discordchan.post(interaction.client, "RULE_REVIEW_CHANNEL_NAME", reviews.mirror_flagged(item))

    @rule_group.command(name="flags", description="(Admin) List rules flagged for review")
    @app_commands.describe(include_resolved="Also show resolved flags")
    async def rule_flags_cmd(interaction: discord.Interaction, include_resolved: bool = False):
        if not _admin_only(interaction):
            await interaction.response.send_message("Only an admin can do that.", ephemeral=True)
            return
        await interaction.response.send_message(reviews.format_list(reviews.list_items(include_resolved)), ephemeral=True)

    @rule_group.command(name="resolve", description="(Admin) Mark a review flag as resolved")
    @app_commands.describe(flag="The flag number, e.g. 7", resolution="Optional note on what was decided")
    async def rule_resolve_cmd(interaction: discord.Interaction, flag: int, resolution: Optional[str] = None):
        if not _admin_only(interaction):
            await interaction.response.send_message("Only an admin can do that.", ephemeral=True)
            return
        item, msg = reviews.resolve(flag, interaction.user.display_name, resolution or "")
        await interaction.response.send_message(msg, ephemeral=True)
        if item:
            await discordchan.post(interaction.client, "RULE_REVIEW_CHANNEL_NAME", reviews.mirror_resolved(item, interaction.user.display_name))

    @results_group.command(name="post", description="(Admin) Post the latest race results and season standings")
    async def results_post_cmd(interaction: discord.Interaction):
        if not admins.is_admin(_caller_id(interaction)):
            await interaction.response.send_message("Only an admin can do that.", ephemeral=True)
            return
        await _run_text(interaction, results.post_latest)

    @feedback_group.command(name="bug", description="Report a bug with the bot")
    @app_commands.describe(description="What went wrong")
    async def feedback_bug_cmd(interaction: discord.Interaction, description: str):
        async def work():
            result = feedback.report_bug(description, interaction.user.display_name, _caller_id(interaction))
            if result.notify:
                await discordchan.post(interaction.client, "FEEDBACK_CHANNEL_NAME", result.notify)
            return str(result)
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(await work(), ephemeral=True)

    @feedback_group.command(name="feature", description="Suggest a feature for the bot")
    @app_commands.describe(description="What you'd like it to do")
    async def feedback_feature_cmd(interaction: discord.Interaction, description: str):
        async def work():
            result = feedback.request_feature(description, interaction.user.display_name, _caller_id(interaction))
            if result.notify:
                await discordchan.post(interaction.client, "FEEDBACK_CHANNEL_NAME", result.notify)
            return str(result)
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(await work(), ephemeral=True)

    @tree.command(name="new-member-test", description="(Admin) Simulate a new member joining: DMs them Ricky's driver-profile interview")
    @app_commands.describe(member="Who to run it on (defaults to you). Restarts it if they already did one.")
    async def new_member_cmd(interaction: discord.Interaction, member: Optional[discord.Member] = None):
        if not admins.is_admin(_caller_id(interaction)):
            await interaction.response.send_message("Only an admin can do that.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        target = member or interaction.user
        status = await profiles.start(target, interaction.client, force=True)
        await interaction.followup.send(f"{status} (Answers will post to #{os.environ.get('PROFILE_CHANNEL_NAME', 'new-driver-profiles')}.)", ephemeral=True)

    @tree.command(name="numbers", description="(Admin) Show which car numbers Ricky sees as taken")
    async def numbers_cmd(interaction: discord.Interaction):
        if not admins.is_admin(_caller_id(interaction)):
            await interaction.response.send_message("Only an admin can do that.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        info = await numbers.refresh(interaction.client)
        if info is None:
            await interaction.followup.send(
                f"I can't read #{os.environ.get('SIGNUP_CHANNEL_NAME')} and have no saved list. Check my View + Read History permission there.",
                ephemeral=True)
            return
        taken, skipped, source = info
        sample = ", ".join(f"#{n} {taken[n][:20]}" for n in sorted(taken)[:12])
        text = (f"Source: {source}. **{len(taken)}** numbers taken (max number {numbers.number_max()}).\n"
                f"First few: {sample}\n"
                f"Lines I couldn't read a number from: {len(skipped)}"
                + (f" (e.g. `{skipped[0]}`)" if skipped else ""))
        await interaction.followup.send(text[:1900], ephemeral=True)

    for group in (rule_group, results_group, feedback_group):
        tree.add_command(group)
