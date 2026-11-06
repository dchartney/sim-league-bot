"""Pulls race results and season standings from Sim Racer Hub for `/results
post`. Three different scraping techniques are needed because the site
mixes server-rendered HTML with client-rendered React, discovered by
checking each page's raw (pre-JS) HTML directly - see README for the
full writeup:

- series_seasons.php: the season list is built client-side from a JS
  array embedded in the page (`curr_season_id=...; seasons=[...]`), so we
  regex that out of the raw HTML instead of parsing a <table>.
- season_schedule.php: plain server-rendered HTML <table> - BeautifulSoup
  works directly.
- season_race.php (single race results): also plain server-rendered HTML.
- season_standings.php: looks like a <table> page but the actual numbers
  are fetched by a React component via a JSON endpoint, get_standings.php,
  AFTER the page loads - the raw HTML never has them. Found the endpoint
  by pulling standingsTable.bundle.js and grepping for where it builds
  the fetch URL. Calling that JSON endpoint directly is how standings are
  actually read here, not table-parsing season_standings.php itself.

Deliberately a low-frequency, admin-triggered scraper (/results post), not
a poller - be polite to Sim Racer Hub.
"""
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import httpx
from bs4 import BeautifulSoup

BASE = "https://www.simracerhub.com/scoring"
SERIES_IDS = {"nextgen": 9866, "arca": 12679}

_CURR_SEASON_RE = re.compile(r"curr_season_id\s*=\s*(\d+)\s*;")
_SEASON_NAME_RE = re.compile(r'\{id:(\d+),sname:"([^"]*)"')


def _get(path: str, **params) -> httpx.Response:
    resp = httpx.get(f"{BASE}/{path}", params=params, timeout=15)
    resp.raise_for_status()
    return resp


def current_season(series_id: int) -> Optional[dict]:
    """Returns {"season_id": int, "name": str} for the series' current
    season, or None if the series/page is unreachable."""
    html = _get("series_seasons.php", series_id=series_id).text
    m = _CURR_SEASON_RE.search(html)
    if not m:
        return None
    season_id = int(m.group(1))
    name_match = next((nm for sid, nm in _SEASON_NAME_RE.findall(html) if int(sid) == season_id), None)
    return {"season_id": season_id, "name": name_match or f"Season {season_id}"}


@dataclass
class RaceSummary:
    schedule_id: int
    race_num: str
    date: datetime
    track: str
    winner: str


def latest_completed_race(season_id: int) -> Optional[RaceSummary]:
    """The most recently completed race in a season, or None if no race
    in the season has results yet. A race counts as completed if its
    WINNER cell has a real driver link rather than a blank placeholder -
    see README for why this, and not some other column, is the right
    signal (the obvious-looking "Yes"/"No" column next to it means
    something else entirely: whether the race's score can be dropped)."""
    html = _get("season_schedule.php", season_id=season_id).text
    soup = BeautifulSoup(html, "lxml")
    completed = []
    for row in soup.find_all("tr", id=re.compile(r"^sch_\d+$")):
        cells = row.find_all("td")
        if len(cells) < 7:
            continue
        winner_link = cells[6].find("a")
        if not winner_link:
            continue  # not yet run
        schedule_id = int(row["id"].split("_")[1])
        race_num = cells[0].get_text(strip=True)
        date_text = cells[1].get_text(strip=True)
        try:
            date = datetime.strptime(date_text, "%b %d, %Y")
        except ValueError:
            continue
        track_links = cells[4].find_all("a")
        track = track_links[-1].get_text(strip=True) if track_links else cells[4].get_text(" ", strip=True)
        completed.append(RaceSummary(schedule_id, race_num, date, track, winner_link.get_text(strip=True)))
    if not completed:
        return None
    return max(completed, key=lambda r: r.date)


def latest_completed_race_any_series() -> Optional[tuple]:
    """Checks every known series' current season and returns
    (series_key, season, RaceSummary) for whichever has the most
    recently completed race - this is the "/results post auto-detects
    series" behavior David asked for, rather than hardcoding one series."""
    candidates = []
    for key, series_id in SERIES_IDS.items():
        season = current_season(series_id)
        if not season:
            continue
        race = latest_completed_race(season["season_id"])
        if race:
            candidates.append((key, season, race))
    if not candidates:
        return None
    return max(candidates, key=lambda c: c[2].date)


@dataclass
class DriverResult:
    finish: str
    start: str
    car_number: str
    driver: str
    total_points: str
    status: str


def race_results(schedule_id: int, limit: int = 10) -> list:
    """Top `limit` finishers for one race (full details are on the page;
    this is a Discord-sized summary, not every column).

    Column position is NOT fixed - a multiclass race (e.g. Arca's Class
    A/B split) inserts an extra "iRacing License" column that shifts
    everything after it, so columns are found by each header's
    data-sortkey attribute instead of a hardcoded index (caught by
    testing against both a single-class Next Gen race and a multiclass
    Arca race, which otherwise silently mislabels Tot Pts as a license
    class)."""
    html = _get("season_race.php", schedule_id=schedule_id).text
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="driver_table")
    if table is None:
        return []
    header_cells = table.find("thead").find_all("th")
    col = {th.get("data-sortkey"): i for i, th in enumerate(header_cells) if th.get("data-sortkey")}
    required = {"fin", "start", "driver_number", "driver", "tpts", "status"}
    if not required.issubset(col):
        return []
    out = []
    for row in table.find_all("tr", class_="jsTableRow")[:limit]:
        cells = row.find_all("td")
        if len(cells) <= max(col.values()):
            continue
        driver_cell = cells[col["driver"]]
        driver_link = driver_cell.find("a")
        out.append(DriverResult(
            finish=cells[col["fin"]].get_text(strip=True),
            start=cells[col["start"]].get_text(strip=True),
            car_number=cells[col["driver_number"]].get_text(strip=True),
            driver=driver_link.get_text(strip=True) if driver_link else driver_cell.get_text(strip=True),
            total_points=cells[col["tpts"]].get_text(strip=True),
            status=cells[col["status"]].get_text(strip=True),
        ))
    return out


@dataclass
class StandingRow:
    pos: int
    driver: str
    points: int
    wins: int
    poles: int
    top5: int
    top10: int


def season_standings(season_id: int, schedule_id: int, limit: int = 10) -> list:
    """Top `limit` drivers in the season standings as of `schedule_id`
    (normally the latest completed race). Calls get_standings.php
    directly - see the module docstring for why season_standings.php's
    own HTML won't have this data."""
    resp = _get("get_standings.php", season_id=season_id, class_id=0, schedule_id=schedule_id)
    data = resp.json()
    drivers = data.get("drivers", {})
    rps = data.get("rps", {})
    # Sorted by total points directly rather than trusting the API's own
    # "pos1" field - tested against a real multiclass (Arca) season and
    # pos1 did NOT match points-descending order (its exact rule isn't
    # documented and may factor in something other than raw points, e.g.
    # per-class ranking). Points-descending is what "season standings"
    # means in the rule book, so that's what gets shown.
    entries = []
    for drid, entry in rps.items():
        name = drivers.get(drid, {}).get("name") or entry.get("name", "Unknown")
        entries.append((name, entry))
    entries.sort(key=lambda e: -(e[1].get("tpts", 0) or 0))
    rows = []
    for pos, (name, entry) in enumerate(entries[:limit], start=1):
        rows.append(StandingRow(
            pos=pos, driver=name, points=entry.get("tpts", 0),
            wins=entry.get("wins", 0), poles=entry.get("poles", 0),
            top5=entry.get("t5", 0), top10=entry.get("t10", 0),
        ))
    return rows


def format_post(series_key: str, season: dict, race: RaceSummary) -> str:
    results = race_results(race.schedule_id, limit=10)
    standings = season_standings(season["season_id"], race.schedule_id, limit=10)

    lines = [
        f"**Checkers flew at {race.track}!** ({season['name']} - {series_key.upper()}, Race {race.race_num}, {race.date.strftime('%b')} {race.date.day}, {race.date.year})",
        "",
        f"**Race winner: {race.winner}**",
        "",
        "**Top 10:**",
    ]
    for r in results[:10]:
        lines.append(f"{r.finish}. {r.driver} ({r.total_points} pts)")

    lines += ["", "**Top 10 season standings:**"]
    for s in standings[:10]:
        lines.append(f"{s.pos}. {s.driver} - {s.points} pts ({s.wins}W, {s.poles}P, {s.top5} T5, {s.top10} T10)")

    return "\n".join(lines)


def post_latest() -> str:
    found = latest_completed_race_any_series()
    if not found:
        return "Couldn't find a completed race in either series on Sim Racer Hub."
    series_key, season, race = found
    try:
        return format_post(series_key, season, race)
    except Exception as exc:
        print(f"ERROR: results.post_latest failed: {exc!r}", file=sys.stderr)
        raise
