"""Discord side of chat-based rule editing: posts the proposed change with
Confirm / Cancel buttons and applies it on confirmation. Nothing is written
until an admin presses Confirm, and only the admin who asked can press it.
The model only ever PROPOSES (nl.propose_rule_changes tool); this module and
rule_changes.apply() are the only code that changes the rule book.

Buttons live in memory: if the bot restarts while a proposal is pending, the
buttons stop working and the admin just asks again."""
import asyncio
import sys

import discord

from . import admins, discordchan, rule_changes, rules

_apply_lock = asyncio.Lock()
PREVIEW_DIFF_CHARS = 1100


def _trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit("\n", 1)[0] + f"\n... ({len(text) - limit} more characters in the commit)"


def preview_text(plan: rule_changes.Plan) -> str:
    parts = [
        "**Proposed rule change** - nothing is changed until you press Confirm.",
        f"Changes: {plan.summary}",
        "```diff\n" + _trim(plan.diff, PREVIEW_DIFF_CHARS) + "\n```",
    ]
    if plan.new_dangling:
        parts.append("**Heads up - this leaves broken references:** " + "; ".join(plan.new_dangling[:5]))
    if plan.warnings:
        parts.append("**Check these rules, their meaning may change:** " + "; ".join(plan.warnings[:5]))
    return "\n".join(parts)[:1990]


class ConfirmView(discord.ui.View):
    def __init__(self, plan: rule_changes.Plan, proposer: discord.abc.User):
        super().__init__(timeout=15 * 60)
        self.plan = plan
        self.proposer_id = proposer.id
        self.message = None  # set by post_proposal

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.proposer_id:
            await interaction.response.send_message("Only the admin who asked for this change can confirm or cancel it.", ephemeral=True)
            return False
        if not admins.is_admin(str(interaction.user.id)):
            await interaction.response.send_message("Only an admin can change the rule book.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        if self.message is not None:
            try:
                await self.message.edit(content=self.message.content + "\n*Expired - ask me again if you still want it.*", view=None)
            except Exception:
                pass

    @discord.ui.button(label="Confirm change", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        self.stop()
        name = interaction.user.display_name
        email = f"discord-{interaction.user.id}@users.noreply.scsra"
        async with _apply_lock:
            try:
                commit = await asyncio.to_thread(rule_changes.apply, self.plan, name, email)
            except Exception as exc:
                print(f"ERROR: rule change apply failed: {exc!r}", file=sys.stderr)
                await interaction.message.edit(content=f"Couldn't apply that: {exc}\nNothing was changed.", view=None)
                return
            pushed, note = await asyncio.to_thread(rules.push_edits)
        status = note if pushed else f"saved here, but NOT on GitHub yet ({note})"
        await interaction.message.edit(
            content=f"**Done** - {self.plan.summary} (commit `{commit}`; {status}). The rule book is live.",
            view=None,
        )
        await discordchan.post(
            interaction.client, "RULEBOOK_LOG_CHANNEL_NAME",
            f"**Rule book changed** by {name} (commit `{commit}`, {status}): {self.plan.summary}\n"
            f"```diff\n{_trim(self.plan.diff, 1300)}\n```",
        )
        if not pushed:
            await discordchan.post(interaction.client, "FEEDBACK_CHANNEL_NAME", f"Rule change {commit} was NOT pushed to GitHub: {note}")

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(content="Cancelled - the rule book is unchanged.", view=None)


async def post_proposal(message: discord.Message, plan: rule_changes.Plan) -> None:
    view = ConfirmView(plan, message.author)
    view.message = await message.channel.send(preview_text(plan), view=view)
