"""Discord approver bot — approve/reject plan adjustments from your phone.

The problem it solves: the coach proposes plan changes (into the git-ignored
plan_adjustments.json ledger), but approving one meant SSHing into the server and
running a CLI command. This bot turns that into a tap.

How it fits the rest of the system:
  - The cron job (sync.py) is UNCHANGED — it still just writes pending proposals
    to the ledger. It knows nothing about Discord.
  - This is a SEPARATE, long-running process. It watches the ledger, posts each
    new pending proposal to a channel with Approve / Reject buttons, and on a tap
    calls plan_adjustments.set_status(). It's a thin adapter over the same gate
    the CLI uses — the approval logic lives in one place (plan_adjustments.py).

Design notes:
  - Buttons are PERSISTENT (custom_id-based, re-registered on startup) so they
    keep working across bot restarts.
  - Only DISCORD_APPROVER_ID may decide — auth for a state-changing action.
  - The posted message id is stored back on the ledger record, so a proposal is
    posted exactly once and never duplicated on the next poll.

By default the bot DMs you the proposal (private) — nothing is posted in a
server. To post in a channel instead, set DISCORD_CHANNEL_ID.

One-time setup:
  1. https://discord.com/developers/applications -> New Application -> Bot ->
     Reset Token -> copy into DISCORD_BOT_TOKEN. (No privileged intents needed.)
  2. OAuth2 -> URL Generator -> scopes: bot, applications.commands; permissions:
     Send Messages, Embed Links. Open the URL and add the bot to ANY server you
     are both in — a bot can only DM a user it shares a server with. (The DMs are
     private; nothing is posted in that server.)
  3. Enable Developer Mode (User Settings -> Advanced). Right-click yourself ->
     Copy ID -> DISCORD_APPROVER_ID. Make sure that server allows DMs from
     members (Server -> Privacy Settings -> "Direct Messages") so the bot can
     reach you. (Optional: right-click a channel -> Copy ID -> DISCORD_CHANNEL_ID
     to post there instead of DMing.)
  4. pip install discord.py, then:  python discord_bot.py
     Run it persistently on the server so it's always listening — e.g. a systemd
     service, or a quick start with:  nohup python discord_bot.py &
"""

from __future__ import annotations

import discord
from discord.ext import commands, tasks

import config
import plan_adjustments

POLL_SECONDS = 30  # how often to check the ledger for new proposals to announce

intents = discord.Intents.default()  # default is enough: no message-content intent
bot = commands.Bot(command_prefix="!", intents=intents)


def _embed(adj: dict, decided: str | None = None) -> discord.Embed:
    """Render one adjustment as a Discord embed (orange pending, green/red once
    decided)."""
    if decided is None:
        color = discord.Color.orange()
    elif "Approved" in decided:
        color = discord.Color.green()
    else:
        color = discord.Color.red()
    embed = discord.Embed(title="Proposed plan adjustment", color=color)
    embed.add_field(name="For date", value=adj.get("for_date") or "?", inline=True)
    embed.add_field(name="Type", value=f"{adj.get('type', '?')} ({adj.get('impact', '?')})", inline=True)
    embed.add_field(name="Change", value=adj.get("summary") or "—", inline=False)
    embed.add_field(name="Why", value=(adj.get("rationale") or "—")[:1024], inline=False)
    footer = f"id {adj.get('id', '?')}"
    if decided:
        footer += f"  ·  {decided}"
    embed.set_footer(text=footer)
    return embed


async def _decide(interaction: discord.Interaction, adj_id: str, status: str) -> None:
    """Shared handler for both buttons: authorize, flip the ledger, update the
    message."""
    if config.DISCORD_APPROVER_ID and interaction.user.id != config.DISCORD_APPROVER_ID:
        await interaction.response.send_message("You're not authorized to decide on this.", ephemeral=True)
        return
    if not plan_adjustments.set_status(adj_id, status):
        await interaction.response.send_message(f"Couldn't find adjustment {adj_id} (already decided?).", ephemeral=True)
        return
    adj = plan_adjustments.get(adj_id) or {"id": adj_id}
    decided = "✅ Approved" if status == "approved" else "❌ Rejected"
    # Remove the buttons (view=None) and recolor the embed to show the outcome.
    await interaction.response.edit_message(embed=_embed(adj, decided=decided), view=None)


class _DecisionButton(discord.ui.Button):
    def __init__(self, adj_id: str, status: str, label: str, style: discord.ButtonStyle):
        super().__init__(label=label, style=style, custom_id=f"adj:{status}:{adj_id}")
        self.adj_id = adj_id
        self.status = status

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await _decide(interaction, self.adj_id, self.status)


class ApprovalView(discord.ui.View):
    """Persistent (timeout=None) view with per-adjustment custom_ids, so the
    buttons keep working after a bot restart once re-registered on startup."""

    def __init__(self, adj_id: str):
        super().__init__(timeout=None)
        self.add_item(_DecisionButton(adj_id, "approved", "Approve", discord.ButtonStyle.success))
        self.add_item(_DecisionButton(adj_id, "rejected", "Reject", discord.ButtonStyle.danger))


async def _destination():
    """Where proposals go: a channel if DISCORD_CHANNEL_ID is set, otherwise a
    private DM to the approver (the default). A bot can only DM a user it shares
    a server with, so the bot must be in a mutual server either way."""
    if config.DISCORD_CHANNEL_ID:
        return bot.get_channel(config.DISCORD_CHANNEL_ID)
    if config.DISCORD_APPROVER_ID:
        return await bot.fetch_user(config.DISCORD_APPROVER_ID)  # opens a DM channel
    return None


@tasks.loop(seconds=POLL_SECONDS)
async def announce_pending() -> None:
    """Send any pending proposal that hasn't been sent yet (DM or channel), then
    record its message id on the ledger so it's never sent twice.

    Every step is guarded: a transient Discord error on one proposal must not
    kill the loop (discord.ext.tasks stops a loop on an unhandled exception),
    which would silently halt all future delivery until a restart."""
    try:
        target = await _destination()
    except Exception as exc:
        print(f"(could not resolve Discord destination: {exc})")
        return
    if target is None:
        return
    for adj in plan_adjustments.pending():
        if adj.get("discord_message_id"):
            continue
        try:
            message = await target.send(embed=_embed(adj), view=ApprovalView(adj["id"]))
            plan_adjustments.annotate(adj["id"], discord_message_id=message.id)
        except Exception as exc:
            # Leave it un-annotated so the next poll retries this proposal.
            print(f"(failed to send proposal {adj.get('id')}, will retry: {exc})")


@announce_pending.before_loop
async def _before_announce() -> None:
    await bot.wait_until_ready()


@bot.event
async def on_ready() -> None:
    # Re-register persistent views for already-posted pending proposals so their
    # buttons still respond after a restart.
    for adj in plan_adjustments.pending():
        if adj.get("discord_message_id"):
            bot.add_view(ApprovalView(adj["id"]), message_id=adj["discord_message_id"])
    try:
        await bot.tree.sync()
    except Exception as exc:  # pragma: no cover
        print(f"(slash-command sync failed: {exc})")
    if not announce_pending.is_running():
        announce_pending.start()
    print(f"Approver bot ready as {bot.user}. Watching for proposals.")


@bot.tree.command(name="pending", description="Show plan adjustments awaiting your approval.")
async def pending_cmd(interaction: discord.Interaction) -> None:
    if config.DISCORD_APPROVER_ID and interaction.user.id != config.DISCORD_APPROVER_ID:
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return
    items = plan_adjustments.pending()
    if not items:
        await interaction.response.send_message("No pending adjustments — you're all caught up. 🎉", ephemeral=True)
        return
    lines = [f"**{len(items)} pending** (posted in this channel with buttons):"]
    for a in items:
        lines.append(f"• `{a.get('for_date','?')}` — {a.get('summary','')} (`{a.get('id')}`)")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


def main() -> None:
    if not config.DISCORD_BOT_TOKEN:
        raise SystemExit("DISCORD_BOT_TOKEN is not set. Add it to .env (see discord_bot.py header).")
    if not config.DISCORD_APPROVER_ID:
        raise SystemExit(
            "DISCORD_APPROVER_ID is not set. It's needed to DM you the proposals "
            "and to authorize approvals. Add it to .env (see discord_bot.py header)."
        )
    bot.run(config.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
