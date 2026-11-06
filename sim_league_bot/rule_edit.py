"""Discord modal for editing a single numbered rule. Deliberately edits
one leaf rule at a time, not a whole section - section files run well
past Discord's ~4000 character modal field limit (checked: the Start/
Flags/Restarts section alone is several times that), but a single rule
paragraph comfortably fits. Every submit becomes a git commit (see
rules.py) - that commit history is this project's rulebook version
control, not a separate database."""
import discord

from . import admins, discordchan, rules


class RuleEditModal(discord.ui.Modal):
    def __init__(self, rule_number: str, current_text: str):
        super().__init__(title=f"Edit rule {rule_number}")
        self.rule_number = rule_number
        self.text_input = discord.ui.TextInput(
            label=f"Rule {rule_number}",
            style=discord.TextStyle.paragraph,
            default=current_text,
            max_length=4000,
        )
        self.add_item(self.text_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        editor_name = interaction.user.display_name
        editor_email = f"discord-{interaction.user.id}@users.noreply.scsra"
        try:
            commit = rules.replace_rule(self.rule_number, self.text_input.value, editor_name, editor_email)
        except (ValueError, RuntimeError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(f"Rule {self.rule_number} updated (commit `{commit}`).", ephemeral=True)
        await discordchan.post(
            interaction.client, "RULEBOOK_LOG_CHANNEL_NAME",
            f"**Rule {self.rule_number} edited** by {editor_name} (commit `{commit}`):\n```{self.text_input.value}```",
        )


async def start_edit(interaction: discord.Interaction, rule_number: str) -> None:
    if not admins.is_admin(str(interaction.user.id)):
        await interaction.response.send_message("Only an admin can edit the rule book.", ephemeral=True)
        return
    block = rules.find_rule(rule_number)
    if block is None:
        await interaction.response.send_message(f"No rule {rule_number} found.", ephemeral=True)
        return
    await interaction.response.send_modal(RuleEditModal(rule_number, block.text))
