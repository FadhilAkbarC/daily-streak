#!/usr/bin/env python3
"""Generate a lightweight GitHub daily streak SVG from historical + recent activity."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Dict, Iterable, List


# Centralized API endpoints keep URL changes in one place.
GITHUB_API = "https://api.github.com"
GITHUB_GRAPHQL = "https://api.github.com/graphql"


@dataclass
class StreakStats:
    """Summary values displayed in the generated SVG card."""

    total_contributions: int
    current_streak: int
    longest_streak: int
    current_start: dt.date | None
    current_end: dt.date | None
    longest_start: dt.date | None
    longest_end: dt.date | None


def build_headers(token: str | None) -> Dict[str, str]:
    """Build consistent headers for every GitHub request."""

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Daily-Streaks-Bot",
    }
    # Token is optional, but recommended for higher rate limits.
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def parse_next_url(link_header: str | None) -> str | None:
    """Extract rel=next URL from GitHub Link header."""

    if not link_header:
        return None

    for chunk in link_header.split(","):
        part = chunk.strip()
        if 'rel="next"' not in part:
            continue
        start = part.find("<")
        end = part.find(">")
        if start != -1 and end != -1 and end > start:
            return part[start + 1 : end]
    return None


def github_get_json(url: str, token: str | None) -> dict | list:
    """Small GET helper with normalized network/error handling."""

    request = urllib.request.Request(url, headers=build_headers(token))
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        msg = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"GitHub API error {exc.code}: {msg}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error: {exc}") from exc


def github_graphql(query: str, variables: dict, token: str | None) -> dict:
    """POST GraphQL helper used for long-range contribution history."""

    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    headers = build_headers(token)
    headers["Content-Type"] = "application/json"

    request = urllib.request.Request(GITHUB_GRAPHQL, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        msg = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"GitHub GraphQL error {exc.code}: {msg}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error: {exc}") from exc

    if payload.get("errors"):
        raise RuntimeError(f"GitHub GraphQL returned errors: {payload['errors']}")

    return payload.get("data", {})


def fetch_user_created_date(username: str, token: str | None) -> dt.date:
    """Get account creation date to define the true streak baseline."""

    payload = github_get_json(f"{GITHUB_API}/users/{username}", token)
    created_at = payload.get("created_at")
    if not created_at:
        raise RuntimeError("Unable to read user.created_at from GitHub API")
    return dt.datetime.fromisoformat(created_at.replace("Z", "+00:00")).date()


def fetch_historical_contribution_days(
    username: str,
    token: str | None,
    start_date: dt.date,
    end_date: dt.date,
) -> Dict[dt.date, int]:
    """Fetch daily contribution counts from account creation until today."""

    # GitHub contributionCollection is safest in ~1-year slices.
    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          contributionCalendar {
            weeks {
              contributionDays {
                date
                contributionCount
              }
            }
          }
        }
      }
    }
    """

    contributions_by_day: Dict[dt.date, int] = {}
    cursor = start_date

    while cursor <= end_date:
        chunk_end = min(cursor + dt.timedelta(days=364), end_date)
        variables = {
            "login": username,
            "from": f"{cursor.isoformat()}T00:00:00Z",
            "to": f"{chunk_end.isoformat()}T23:59:59Z",
        }
        data = github_graphql(query, variables, token)

        user = data.get("user") or {}
        collection = user.get("contributionsCollection") or {}
        calendar = collection.get("contributionCalendar") or {}
        weeks = calendar.get("weeks") or []

        # Flatten nested weeks/day nodes into a simple date->count map.
        for week in weeks:
            for day in week.get("contributionDays", []):
                day_date = dt.date.fromisoformat(day["date"])
                count = int(day.get("contributionCount", 0))
                contributions_by_day[day_date] = contributions_by_day.get(day_date, 0) + count

        cursor = chunk_end + dt.timedelta(days=1)

    return contributions_by_day


def fetch_recent_push_events(
    username: str,
    token: str | None,
    max_pages: int = 5,
    lookback_days: int = 45,
) -> List[dict]:
    """Fetch recent public events so same-day pushes appear quickly."""

    # Hard bounds prevent accidental over-fetching and API abuse.
    max_pages = max(1, min(max_pages, 10))
    lookback_days = max(1, lookback_days)

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=lookback_days)
    params = urllib.parse.urlencode({"per_page": 100})
    next_url: str | None = f"{GITHUB_API}/users/{username}/events/public?{params}"

    events: List[dict] = []
    page_count = 0

    while next_url and page_count < max_pages:
        request = urllib.request.Request(next_url, headers=build_headers(token))

        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
                link_header = response.headers.get("Link")
        except urllib.error.HTTPError as exc:
            msg = exc.read().decode("utf-8", errors="ignore")
            # Endpoint may return 422 when pagination depth limit is hit.
            if exc.code == 422 and "pagination is limited" in msg.lower():
                break
            raise RuntimeError(f"GitHub API error {exc.code}: {msg}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Network error: {exc}") from exc

        page_count += 1
        if not payload:
            break

        events.extend(payload)
        next_url = parse_next_url(link_header)

        # Early-stop once a page is older than configured lookback window.
        oldest = None
        for event in payload:
            created_at = event.get("created_at")
            if not created_at:
                continue
            ts = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            oldest = ts if oldest is None else min(oldest, ts)
        if oldest and oldest < cutoff:
            break

    return events


def aggregate_push_commits_by_day(events: Iterable[dict], timezone: dt.timezone) -> Dict[dt.date, int]:
    """Aggregate PushEvent commit counts by local date."""

    push_counts: Dict[dt.date, int] = {}

    for event in events:
        # We only use push events to represent commit-log activity.
        if event.get("type") != "PushEvent":
            continue

        # Count commits attached to this push payload.
        commit_count = len(event.get("payload", {}).get("commits", []))
        if commit_count <= 0:
            continue

        created_at = event.get("created_at")
        if not created_at:
            continue

        # Convert timestamp into requested local day boundary.
        timestamp = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        local_day = timestamp.astimezone(timezone).date()
        push_counts[local_day] = push_counts.get(local_day, 0) + commit_count

    return push_counts


def merge_historical_with_recent(
    historical_days: Dict[dt.date, int],
    recent_push_days: Dict[dt.date, int],
) -> Dict[dt.date, int]:
    """Merge stable history with recent pushes for fresher same-day values."""

    merged = dict(historical_days)

    # Use max() to avoid inflating counts while still patching fresh days.
    for day, push_count in recent_push_days.items():
        merged[day] = max(merged.get(day, 0), push_count)

    return merged


def compute_streaks(contributions_by_day: Dict[dt.date, int], today: dt.date) -> StreakStats:
    """Compute current + longest streak from continuous active-day runs."""

    # Only days with at least one contribution participate in streaks.
    active_days = sorted(day for day, count in contributions_by_day.items() if count > 0)
    if not active_days:
        return StreakStats(0, 0, 0, None, None, None, None)

    total_contributions = sum(count for count in contributions_by_day.values() if count > 0)

    longest_len = 1
    longest_start = active_days[0]
    longest_end = active_days[0]

    run_start = active_days[0]
    prev = active_days[0]

    # Walk every active day and split runs when there is a gap.
    for day in active_days[1:]:
        if (day - prev).days == 1:
            prev = day
            continue

        run_len = (prev - run_start).days + 1
        if run_len > longest_len:
            longest_len = run_len
            longest_start = run_start
            longest_end = prev

        run_start = day
        prev = day

    # Final run check after loop completion.
    run_len = (prev - run_start).days + 1
    if run_len > longest_len:
        longest_len = run_len
        longest_start = run_start
        longest_end = prev

    latest = active_days[-1]

    # Current streak is considered alive when latest activity is today or yesterday.
    if (today - latest).days > 1:
        current_len = 0
        current_start = None
        current_end = None
    else:
        current_end = latest
        current_start = latest
        active_set = set(active_days)

        # Extend backward while days are contiguous.
        while current_start - dt.timedelta(days=1) in active_set:
            current_start -= dt.timedelta(days=1)
        current_len = (current_end - current_start).days + 1

    return StreakStats(
        total_contributions=total_contributions,
        current_streak=current_len,
        longest_streak=longest_len,
        current_start=current_start,
        current_end=current_end,
        longest_start=longest_start,
        longest_end=longest_end,
    )


def day_fmt(day: dt.date) -> str:
    """Platform-safe long date label."""

    return day.strftime("%b %-d, %Y") if os.name != "nt" else day.strftime("%b %d, %Y")


def short_day_fmt(day: dt.date) -> str:
    """Platform-safe short date label."""

    return day.strftime("%b %-d") if os.name != "nt" else day.strftime("%b %d")


def format_range(start: dt.date | None, end: dt.date | None) -> str:
    """Render a compact date-range label for the card."""

    if not start or not end:
        return "No active streak"
    if start == end:
        return day_fmt(start)
    if start.year == end.year:
        return f"{short_day_fmt(start)} - {day_fmt(end)}"
    return f"{day_fmt(start)} - {day_fmt(end)}"


def render_svg(username: str, stats: StreakStats) -> str:
    """Build the SVG card that is embedded in profile README files."""

    return f"""<svg width=\"900\" height=\"220\" viewBox=\"0 0 900 220\" preserveAspectRatio=\"xMidYMid meet\" style=\"max-width:900px;width:100%;height:auto;\" xmlns=\"http://www.w3.org/2000/svg\" role=\"img\" aria-label=\"GitHub streak stats\">
  <defs>
    <linearGradient id=\"bg\" x1=\"0\" x2=\"1\" y1=\"0\" y2=\"1\">
      <stop offset=\"0%\" stop-color=\"#0d1117\"/>
      <stop offset=\"100%\" stop-color=\"#161b22\"/>
    </linearGradient>
  </defs>
  <rect width=\"900\" height=\"220\" rx=\"18\" fill=\"url(#bg)\"/>
  <text x=\"450\" y=\"32\" text-anchor=\"middle\" font-family=\"Segoe UI, Ubuntu, sans-serif\" font-size=\"20\" fill=\"#58a6ff\">{username} • Daily-Streaks</text>

  <line x1=\"300\" y1=\"55\" x2=\"300\" y2=\"195\" stroke=\"#30363d\"/>
  <line x1=\"600\" y1=\"55\" x2=\"600\" y2=\"195\" stroke=\"#30363d\"/>

  <text x=\"150\" y=\"98\" text-anchor=\"middle\" font-family=\"Segoe UI, Ubuntu, sans-serif\" font-weight=\"700\" font-size=\"52\" fill=\"#c9d1d9\">{stats.total_contributions}</text>
  <text x=\"150\" y=\"135\" text-anchor=\"middle\" font-family=\"Segoe UI, Ubuntu, sans-serif\" font-size=\"28\" fill=\"#8b949e\">Total Daily Contributions</text>

  <text x=\"450\" y=\"88\" text-anchor=\"middle\" font-family=\"Segoe UI Emoji, Segoe UI Symbol\" font-size=\"34\" fill=\"#ffa657\">🔥</text>
  <text x=\"450\" y=\"118\" text-anchor=\"middle\" font-family=\"Segoe UI, Ubuntu, sans-serif\" font-weight=\"700\" font-size=\"54\" fill=\"#ffa657\">{stats.current_streak}</text>
  <text x=\"450\" y=\"154\" text-anchor=\"middle\" font-family=\"Segoe UI, Ubuntu, sans-serif\" font-size=\"30\" fill=\"#ffa657\">Current Streak (days)</text>
  <text x=\"450\" y=\"184\" text-anchor=\"middle\" font-family=\"Segoe UI, Ubuntu, sans-serif\" font-size=\"21\" fill=\"#8b949e\">{format_range(stats.current_start, stats.current_end)}</text>

  <text x=\"750\" y=\"98\" text-anchor=\"middle\" font-family=\"Segoe UI, Ubuntu, sans-serif\" font-weight=\"700\" font-size=\"52\" fill=\"#c9d1d9\">{stats.longest_streak}</text>
  <text x=\"750\" y=\"135\" text-anchor=\"middle\" font-family=\"Segoe UI, Ubuntu, sans-serif\" font-size=\"28\" fill=\"#8b949e\">Longest Streak (days)</text>
  <text x=\"750\" y=\"170\" text-anchor=\"middle\" font-family=\"Segoe UI, Ubuntu, sans-serif\" font-size=\"21\" fill=\"#8b949e\">{format_range(stats.longest_start, stats.longest_end)}</text>
</svg>
"""


def parse_timezone(offset_hours: int) -> dt.timezone:
    """Create timezone from integer UTC offset."""

    return dt.timezone(dt.timedelta(hours=offset_hours))


def main() -> int:
    """CLI entrypoint used by local runs and GitHub Actions."""

    parser = argparse.ArgumentParser(description="Generate GitHub daily streak SVG")
    parser.add_argument("--username", required=True, help="GitHub username")
    parser.add_argument("--token", default=os.getenv("GITHUB_TOKEN"), help="GitHub API token")
    parser.add_argument("--timezone-offset", type=int, default=0, help="UTC offset in hours")
    parser.add_argument("--max-pages", type=int, default=5, help="Max event pages for recent pushes (1-10)")
    parser.add_argument("--lookback-days", type=int, default=45, help="Recent event lookback window")
    parser.add_argument("--output", default="assets/github-streak.svg", help="Output SVG path")
    parser.add_argument("--events-file", help="Optional local JSON events file (offline testing mode)")
    args = parser.parse_args()

    timezone = parse_timezone(args.timezone_offset)
    today = dt.datetime.now(timezone).date()

    # Offline mode is for deterministic local testing using a fixture file.
    if args.events_file:
        with open(args.events_file, "r", encoding="utf-8") as fh:
            events = json.load(fh)
        historical_days: Dict[dt.date, int] = {}
        recent_push_days = aggregate_push_commits_by_day(events, timezone)
        merged_days = merge_historical_with_recent(historical_days, recent_push_days)
    else:
        # Online mode builds full-history baseline from account creation date.
        profile_created = fetch_user_created_date(args.username, args.token)
        historical_days = fetch_historical_contribution_days(
            username=args.username,
            token=args.token,
            start_date=profile_created,
            end_date=today,
        )

        # Overlay recent pushes for faster same-day visibility.
        recent_events = fetch_recent_push_events(
            username=args.username,
            token=args.token,
            max_pages=args.max_pages,
            lookback_days=args.lookback_days,
        )
        recent_push_days = aggregate_push_commits_by_day(recent_events, timezone)
        merged_days = merge_historical_with_recent(historical_days, recent_push_days)

    stats = compute_streaks(merged_days, today)

    svg = render_svg(args.username, stats)
    out_path = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(svg)

    # JSON output is convenient for workflow logs/debugging.
    print(
        json.dumps(
            {
                "username": args.username,
                "total_contributions": stats.total_contributions,
                "current_streak": stats.current_streak,
                "longest_streak": stats.longest_streak,
                "days_with_contributions": len([d for d, v in merged_days.items() if v > 0]),
                "output": args.output,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
