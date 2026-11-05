#!/usr/bin/env python3
"""Generate GitHub stats / streak / top-languages SVG cards for a profile README.

Self-hosted replacement for github-readme-stats.vercel.app and
streak-stats.demolab.com (both are public shared instances that are often
rate-limited or down). Uses only the Python standard library and the GitHub
GraphQL API.

Env vars:
  GITHUB_TOKEN   token for the GraphQL API (the Actions GITHUB_TOKEN is enough)
  GH_USERNAME    GitHub login to build the cards for
  DISPLAY_NAME   (optional) name shown in the card titles
  OUT_DIR        (optional) output directory, default "assets"
"""
import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from xml.sax.saxutils import escape

API = "https://api.github.com/graphql"

# tokyonight theme
BG = "#1a1b27"
BORDER = "#2f3148"
TITLE = "#70a5fd"
TEXT = "#38bdae"
LABEL = "#a9b1d6"
ICON = "#bf91f3"
ACCENT = "#fe428e"
FONT = "'Segoe UI', Ubuntu, 'Helvetica Neue', Sans-Serif"


# --------------------------------------------------------------------------
# GitHub API
# --------------------------------------------------------------------------
def gql(query, variables):
    token = os.environ["GITHUB_TOKEN"]
    req = urllib.request.Request(
        API,
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profile-readme-cards",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    if data.get("errors"):
        raise RuntimeError(f"GraphQL error: {data['errors']}")
    return data["data"]


PROFILE_Q = """
query($login: String!, $cursor: String) {
  user(login: $login) {
    createdAt
    followers { totalCount }
    pullRequests { totalCount }
    issues { totalCount }
    repositories(ownerAffiliations: OWNER, isFork: false, first: 100, after: $cursor) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        stargazerCount
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""

YEAR_Q = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      restrictedContributionsCount
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def fetch_profile(login):
    cursor, repos, user = None, [], None
    while True:
        u = gql(PROFILE_Q, {"login": login, "cursor": cursor})["user"]
        user = user or u
        repos.extend(u["repositories"]["nodes"])
        page = u["repositories"]["pageInfo"]
        if not page["hasNextPage"]:
            break
        cursor = page["endCursor"]
    user["repo_nodes"] = repos
    return user


def fetch_years(login, created_at, today):
    """Return (commits_total, {date: count}) across the account's whole life."""
    created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    days, commits = {}, 0
    for year in range(created.year, today.year + 1):
        start = max(created, datetime(year, 1, 1, tzinfo=timezone.utc))
        end = min(
            datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
            datetime.now(timezone.utc),
        )
        c = gql(
            YEAR_Q,
            {
                "login": login,
                "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )["user"]["contributionsCollection"]
        commits += c["totalCommitContributions"] + c["restrictedContributionsCount"]
        for week in c["contributionCalendar"]["weeks"]:
            for d in week["contributionDays"]:
                days[date.fromisoformat(d["date"])] = d["contributionCount"]
    return commits, days


# --------------------------------------------------------------------------
# Calculations
# --------------------------------------------------------------------------
def compute_streaks(days, today):
    """days: {date: count}. Returns total, (cur_len, cur_start, cur_end), (best_len, best_start, best_end)."""
    days = {d: c for d, c in days.items() if d <= today}
    total = sum(days.values())

    best = (0, None, None)
    run_len, run_start, prev = 0, None, None
    for d in sorted(days):
        if days[d] > 0:
            if prev is not None and d - prev == timedelta(days=1) and run_len > 0:
                run_len += 1
            else:
                run_len, run_start = 1, d
            if run_len > best[0]:
                best = (run_len, run_start, d)
        else:
            run_len = 0
        prev = d

    # current streak: today may still be empty, so allow a one-day grace
    cursor = today if days.get(today, 0) > 0 else today - timedelta(days=1)
    cur_len, cur_end = 0, None
    while days.get(cursor, 0) > 0:
        cur_end = cur_end or cursor
        cur_len += 1
        cursor -= timedelta(days=1)
    cur = (cur_len, cursor + timedelta(days=1) if cur_len else None, cur_end)
    return total, cur, best


def aggregate_languages(repo_nodes, limit=6):
    sizes, colors = defaultdict(int), {}
    for repo in repo_nodes:
        for e in repo["languages"]["edges"]:
            sizes[e["node"]["name"]] += e["size"]
            colors[e["node"]["name"]] = e["node"]["color"] or "#8b949e"
    total = sum(sizes.values()) or 1
    top = sorted(sizes.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    return [(n, s / total * 100, colors[n]) for n, s in top]


# --------------------------------------------------------------------------
# SVG rendering
# --------------------------------------------------------------------------
def _card(width, height, title, body):
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">
  <rect x="0.5" y="0.5" rx="4.5" width="{width - 1}" height="{height - 1}" fill="{BG}" stroke="{BORDER}"/>
  <text x="25" y="35" font-family="{FONT}" font-size="18" font-weight="600" fill="{TITLE}">{escape(title)}</text>
{body}
</svg>
"""


def stats_svg(name, s):
    rows = [
        ("Total Stars Earned", s["stars"]),
        ("Total Commits", s["commits"]),
        ("Total PRs", s["prs"]),
        ("Total Issues", s["issues"]),
        ("Public Repos", s["repos"]),
        ("Followers", s["followers"]),
    ]
    out = []
    for i, (label, value) in enumerate(rows):
        y = 70 + i * 25
        out.append(
            f'  <circle cx="33" cy="{y - 5}" r="4" fill="{ICON}"/>\n'
            f'  <text x="48" y="{y}" font-family="{FONT}" font-size="14" fill="{LABEL}">{escape(label)}:</text>\n'
            f'  <text x="230" y="{y}" font-family="{FONT}" font-size="14" font-weight="700" fill="{TEXT}">{value:,}</text>'
        )
    return _card(330, 215, f"{name}'s GitHub Stats", "\n".join(out))


def langs_svg(langs):
    width, bar_x, bar_w = 330, 25, 280
    out = [f'  <clipPath id="bar"><rect x="{bar_x}" y="55" width="{bar_w}" height="8" rx="4"/></clipPath>', '  <g clip-path="url(#bar)">']
    x = float(bar_x)
    for _, pct, color in langs:
        w = bar_w * pct / 100
        out.append(f'    <rect x="{x:.2f}" y="55" width="{w:.2f}" height="8" fill="{escape(color)}"/>')
        x += w
    out.append("  </g>")
    for i, (lang, pct, color) in enumerate(langs):
        col, row = i % 2, i // 2
        cx, cy = 25 + col * 150, 92 + row * 25
        out.append(
            f'  <circle cx="{cx + 5}" cy="{cy - 4}" r="5" fill="{escape(color)}"/>\n'
            f'  <text x="{cx + 17}" y="{cy}" font-family="{FONT}" font-size="12" fill="{LABEL}">{escape(lang)} {pct:.1f}%</text>'
        )
    height = 92 + ((len(langs) + 1) // 2) * 25 + 5
    return _card(width, max(height, 130), "Most Used Languages", "\n".join(out))


def _fmt(d):
    return d.strftime("%b %-d, %Y") if d else ""


def streak_svg(total, cur, best, first_day):
    cur_len, cur_s, cur_e = cur
    best_len, best_s, best_e = best
    cols = [
        (83, f"{total:,}", "Total Contributions", f"{_fmt(first_day)} - Present", TEXT),
        (247, str(cur_len), "Current Streak", f"{_fmt(cur_s)} - {_fmt(cur_e)}" if cur_len else "No active streak", ACCENT),
        (411, str(best_len), "Longest Streak", f"{_fmt(best_s)} - {_fmt(best_e)}" if best_len else "-", TEXT),
    ]
    out = []
    for x, big, label, rng, color in cols:
        out.append(
            f'  <text x="{x}" y="58" text-anchor="middle" font-family="{FONT}" font-size="28" font-weight="700" fill="{color}">{big}</text>\n'
            f'  <text x="{x}" y="86" text-anchor="middle" font-family="{FONT}" font-size="14" fill="{LABEL}">{label}</text>\n'
            f'  <text x="{x}" y="108" text-anchor="middle" font-family="{FONT}" font-size="11" fill="{LABEL}" opacity="0.7">{rng}</text>'
        )
    out.insert(0, f'  <line x1="165" y1="28" x2="165" y2="115" stroke="{BORDER}"/>\n  <line x1="329" y1="28" x2="329" y2="115" stroke="{BORDER}"/>')
    # title row replaced by a smaller banner for this card
    body = "\n".join(out)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="495" height="135" viewBox="0 0 495 135" role="img" aria-label="GitHub Streak">
  <rect x="0.5" y="0.5" rx="4.5" width="494" height="134" fill="{BG}" stroke="{BORDER}"/>
{body}
</svg>
"""


# --------------------------------------------------------------------------
def main():
    login = os.environ["GH_USERNAME"]
    name = os.environ.get("DISPLAY_NAME") or login
    out_dir = os.environ.get("OUT_DIR", "assets")
    today = datetime.now(timezone.utc).date()

    user = fetch_profile(login)
    commits, days = fetch_years(login, user["createdAt"], today)
    write_cards(name, out_dir, user, commits, days, today)


def write_cards(name, out_dir, user, commits, days, today):
    stats = {
        "stars": sum(r["stargazerCount"] for r in user["repo_nodes"]),
        "commits": commits,
        "prs": user["pullRequests"]["totalCount"],
        "issues": user["issues"]["totalCount"],
        "repos": user["repositories"]["totalCount"],
        "followers": user["followers"]["totalCount"],
    }
    total, cur, best = compute_streaks(days, today)
    first_day = min(days) if days else today

    os.makedirs(out_dir, exist_ok=True)
    files = {
        "github-stats.svg": stats_svg(name, stats),
        "github-streak.svg": streak_svg(total, cur, best, first_day),
        "github-langs.svg": langs_svg(aggregate_languages(user["repo_nodes"])),
    }
    for fname, svg in files.items():
        with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as f:
            f.write(svg)
        print(f"wrote {os.path.join(out_dir, fname)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # fail loudly so the Action shows red
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
