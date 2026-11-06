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

## Sim Racer Hub scraping
League page: `https://www.simracerhub.com/scoring/league_series.php?league_id=3440`. No official API - `sim_league_bot/results.py` scrapes it, but not uniformly, because the site itself isn't uniform:
- `series_seasons.php` (current season lookup) and `season_standings.php` (season standings) look like ordinary pages but their actual data is rendered client-side by JavaScript/React after load - the raw HTML has no numbers, only a season list (as a JS array) or a config blob handed to a React component. Confirmed by fetching each page's raw HTML directly with `requests`, not through a browser, and finding the data missing.
- `season_schedule.php` and `season_race.php` (race results) ARE plain server-rendered HTML tables - BeautifulSoup works directly on those.
- For standings, rather than render the page with a browser, `results.py` calls the JSON endpoint the React component itself calls (`get_standings.php`) - found by downloading `standingsTable.bundle.js` and grepping for where it builds that URL.
- Race-results columns are read by each header's `data-sortkey` attribute, not a fixed position - a multiclass race (Arca's Class A/B split) inserts an extra column that shifts every later column over by one, which a fixed-index read silently mislabeled during testing.
- Sorts season standings by total points directly rather than trusting the API's own `pos1` field, which did not match points-descending order in a multiclass season when tested - its exact rule isn't documented.

Tested live against real SCSRA data on Sim Racer Hub while building this (2026-11-05): both a single-class Next Gen race and a multiclass Arca race, both season-standings and single-race results.

## Deployment
Runs on its own LXC (VMID 111, Debian 12, IP 10.30.15.176, node BeardsProx), separate from VM 107 (which hosts homelab-mcp/OpenBao) per David's decision. System account `sim-league-bot` (no sudo, no login shell). Provisioned by Albert (Advisor/Actor model). Because it's a separate host from OpenBao, `.env`'s `BAO_ADDR` must be `https://secrets.hartney.me` (the LAN-facing Caddy front door), NOT `http://127.0.0.1:8200` like household-manager uses — that address only works for services running directly on VM 107.

## Status
Design finalized. Built: the rule-book scaffold (git-versioned `rules/*.md`), a first pass at the bot itself (`sim_league_bot/`), its OpenBao AppRole/policy/secrets, a private GitHub repo (`dchartney/sim-league-bot`), and the LXC it will run on.

Not done yet:
- The repo hasn't been cloned onto the LXC / venv set up / systemd service installed there yet (next step).
- The AppRole secret ID (generated when the AppRole was created) still needs to be placed at `/opt/sim-league-bot/.bao-secret-id` on the LXC by David directly, via the Proxmox console — deliberately not routed through any automated channel since nobody else has that value.
- No Discord application has actually been connected to yet (nothing has run against live Discord).
- Channel names in `.env.example` are placeholders - David hasn't picked the real ones yet.
- The exact `/ruling` rate limit numbers in `.env.example` are placeholders, not a number David has confirmed.

See the memory file above for the full design history (persona, hosting, NL access model, cost controls).
