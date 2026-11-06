# SCSRA Discord Bot — "Ricky Bobby"

Discord bot for the Stock Car Sim Racing Association (SCSRA), an iRacing league. Separate from the household-manager ("Bobby Singer") repo — own Discord app, own token, own Anthropic key, own host (Debian LXC).

Design and decision history: `Z:\ClaudeAgent\memory\topics\sim-league-bot.md` (not part of this repo — internal planning notes).

## What it does
- Answers schedule and rules questions via DM (natural language, Claude API).
- `/ruling <scenario>` cites specific rule numbers and explains how they apply; escalates to the admin team instead of guessing when a rule doesn't clearly cover a scenario.
- `/results post` pulls race results + season standings from Sim Racer Hub and posts them.
- Rule book lives in `rules/*.md`, one file per top-level numbered section, edited via a Discord modal (small edits) or an admin-channel file upload (bulk replace) — both commit to this repo's git history, which is the version-history mechanism (tag per season).

## Rule book
Source: `SCSRA Season 10.pdf` (David, 2026-11-05), extracted with `pdfplumber`. Current version per the book's own tracking: **v8.1** ("Changes for Season 10"). The text in `rules/*.md` is verbatim from that PDF, including the source document's own typos — this repo is not the place to silently correct wording; rule changes go through the normal edit flow (modal or file upload) so they're tracked.

Two things carried over as-is from the source PDF, not fixed:
- "Rookie of the Year" has its own heading/TOC entry but is numbered `10.4`, nested under the Owners/Team Championship section rather than getting its own top-level number — see the note in `rules/10-owners-team-championship.md`.
- Two dangling cross-references in the source text: rule `2.8.1` points to a "3.5.3" that doesn't exist in section 3 (which only goes up to 3.5.2), and rule `7.9` points to a "1.5.5" that doesn't exist in section 1 (which only goes up to 1.5.3). Left as written; flag to the league if they want these corrected.

## Status
Design finalized, nothing built yet beyond this rule-book scaffold. See the memory file above for the full plan (persona, hosting, NL access model, cost controls, channels).
