#!/usr/bin/env python3
"""Draw the SVG cards for github.com/NripeshN.

Every image in assets/ comes out of this file. The hero is static; the stats
and chess cards carry live numbers from the GitHub, PyPI, Lichess and FIDE
APIs.

It also rewrites the alt text of the two live cards in README.md (between the
<!-- stats-card --> and <!-- chess-card --> markers), so screen readers get
the same numbers as everyone else.

Run it locally:

    GITHUB_TOKEN="$(gh auth token)" python3 scripts/build_assets.py

or let .github/workflows/refresh.yml run it on Mondays.

Standard library only, so the workflow needs nothing but the runner's python3.
Each live value falls back to the last good one in scripts/data/snapshot.json,
so a flaky API costs a week of freshness rather than a broken profile.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SNAPSHOT = ROOT / "scripts" / "data" / "snapshot.json"
README = ROOT / "README.md"

GITHUB_USER = "NripeshN"
LICHESS_ME = "NripeshNiketan"
LICHESS_BOT = "Nripesh-MetalFish"
FIDE_ID = "48731102"
USER_AGENT = "NripeshN-profile-cards/1.0 (+https://github.com/NripeshN/NripeshN)"

SANS = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', "
        "Helvetica, Arial, sans-serif")
MONO = ("ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, "
        "'Liberation Mono', monospace")

# Colours follow crossgl.net (dark) and GitHub's own light palette, so each
# card sits naturally on either theme. The logo colours are sampled from the
# CrossGL mark.
THEMES: Dict[str, Dict[str, str]] = {
    "dark": {
        "panel": "#0a0a0f",
        "border": "#262636",
        "surface": "#14141d",
        "text": "#f0f0f5",
        "muted": "#9a9ab0",
        "coral": "#ff6b57",
        "cyan": "#22d3ee",
        "violet": "#a78bfa",
        "emerald": "#34d399",
        "logo_coral": "#ff4a33",
        "logo_cyan": "#18c4f2",
        "overlap": "#c04a5f,#6a6fc4,#2f9fd8",
        "glow": "0.22",
        "spoke": "0.55",
        "bar_text": "#0a0a0f",
    },
    "light": {
        "panel": "#f6f8fa",
        "border": "#d0d7de",
        "surface": "#ffffff",
        "text": "#1f2328",
        "muted": "#59636e",
        "coral": "#c9301c",
        "cyan": "#0e7490",
        "violet": "#6d28d9",
        "emerald": "#047857",
        "logo_coral": "#f63b25",
        "logo_cyan": "#06b7e7",
        "overlap": "#b0565f,#56679f,#2f8fc0",
        "glow": "0.13",
        "spoke": "0.6",
        "bar_text": "#ffffff",
    },
}


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

def http_json(url: str, headers: Optional[Dict[str, str]] = None,
              payload: Optional[dict] = None, timeout: int = 20) -> object:
    """GET (or POST when payload is set) a URL and decode its JSON body."""
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    hdrs.update(headers or {})
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    last_error: Optional[Exception] = None
    for attempt in range(2):  # two tries keeps the worst case well inside CI's timeout
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
        except Exception as exc:  # network trouble: wait, then try once more
            last_error = exc
            time.sleep(3)
            continue
        # utf-8-sig also strips the byte-order mark FIDE sends. A body that
        # isn't JSON won't improve on a retry, so it fails straight away.
        return json.loads(body.decode("utf-8-sig"))
    raise RuntimeError(f"{url}: {last_error}")


def github(path: str, token: Optional[str],
           payload: Optional[dict] = None) -> dict:
    url = path if path.startswith("https://") else f"https://api.github.com/{path}"
    headers = {"Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    result = http_json(url, headers=headers, payload=payload)
    if not isinstance(result, dict):
        raise RuntimeError(f"{path}: unexpected response shape")
    return result


def fetch_crosstl_stars(token: Optional[str]) -> dict:
    repo = github("repos/CrossGL/crosstl", token)
    return {"crosstl_stars": int(repo["stargazers_count"])}


def fetch_upstream(token: Optional[str]) -> dict:
    """Merged public PRs in repositories owned by someone else."""
    query = (f"author:{GITHUB_USER} type:pr is:merged is:public "
             f"-user:CrossGL -user:{GITHUB_USER}")
    repos = set()
    total = 0
    for page in range(1, 11):  # the search API stops at 1,000 results
        params = urllib.parse.urlencode(
            {"q": query, "per_page": 100, "page": page})
        res = github(f"https://api.github.com/search/issues?{params}", token)
        if res.get("incomplete_results"):
            raise RuntimeError("search returned incomplete results")
        total = int(res["total_count"])
        items = res.get("items", [])
        repos.update(i["repository_url"].rsplit("/repos/", 1)[1] for i in items)
        if len(items) < 100 or page * 100 >= total:
            break
    return {"upstream_prs": total, "upstream_repos": len(repos)}


def fetch_contributions(token: Optional[str]) -> dict:
    if not token:
        raise RuntimeError("the GraphQL API needs a token")
    query = ("query($login: String!) { user(login: $login) { "
             "contributionsCollection { contributionCalendar { "
             "totalContributions } } } }")
    res = github("graphql", token,
                 payload={"query": query, "variables": {"login": GITHUB_USER}})
    if res.get("errors"):
        raise RuntimeError(res["errors"][0].get("message", "GraphQL error"))
    calendar = res["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    return {"contributions_year": int(calendar["totalContributions"])}


def fetch_pypi(_token: Optional[str]) -> dict:
    info = http_json("https://pypi.org/pypi/crosstl/json")["info"]  # type: ignore[index]
    version = str(info["version"])
    if not re.fullmatch(r"[0-9][0-9A-Za-z.+-]{0,24}", version):
        raise ValueError(f"unexpected version string {version!r}")
    return {"crosstl_version": version}


def fetch_lichess(_token: Optional[str]) -> dict:
    me = http_json(f"https://lichess.org/api/user/{LICHESS_ME}")
    bot = http_json(f"https://lichess.org/api/user/{LICHESS_BOT}")
    return {
        "me_bullet": int(me["perfs"]["bullet"]["rating"]),  # type: ignore[index]
        "bot_bullet": int(bot["perfs"]["bullet"]["rating"]),  # type: ignore[index]
        "bot_games": int(bot["count"]["all"]),  # type: ignore[index]
        "bot_draws": int(bot["count"]["draw"]),  # type: ignore[index]
    }


def fetch_fide(_token: Optional[str]) -> dict:
    """Latest standard rating from the history FIDE's profile chart uses."""
    rows = http_json(
        f"https://ratings.fide.com/a_chart_data.phtml?event={FIDE_ID}&period=1",
        headers={"X-Requested-With": "XMLHttpRequest"})
    dated: List[Tuple[dt.datetime, int]] = []
    for row in rows:  # type: ignore[union-attr]
        rating = str(row.get("rating") or "")
        if rating.isdigit():
            dated.append((dt.datetime.strptime(row["date_2"], "%Y-%b"), int(rating)))
    if not dated:
        raise ValueError("no standard rating in the FIDE history")
    return {"fide_standard": max(dated)[1]}


FETCHERS: List[Tuple[str, Callable[[Optional[str]], dict]]] = [
    ("CrossTL stars", fetch_crosstl_stars),
    ("upstream PRs", fetch_upstream),
    ("contributions", fetch_contributions),
    ("PyPI", fetch_pypi),
    ("Lichess", fetch_lichess),
    ("FIDE", fetch_fide),
]

# Counters that should never fall sharply between two runs. A big drop means
# a glitchy API response, so the last good value is kept instead.
MONOTONIC = {"crosstl_stars", "upstream_prs", "upstream_repos", "bot_games",
             "bot_draws"}


def plausible(key: str, new: object, old: object) -> bool:
    if key in MONOTONIC and isinstance(new, int) and isinstance(old, int):
        return new >= old * 0.9
    return True


def load_snapshot() -> dict:
    if SNAPSHOT.exists():
        snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    else:
        snap = {}
    snap.setdefault("values", {})
    snap.setdefault("as_of", {})
    return snap


def refresh(snap: dict, token: Optional[str], today: str) -> None:
    for name, fetch in FETCHERS:
        try:
            got = fetch(token)
        except Exception as exc:
            print(f"warning: {name} not refreshed ({exc}); keeping last good value",
                  file=sys.stderr)
            continue
        for key, value in got.items():
            old = snap["values"].get(key)
            if old is not None and not plausible(key, value, old):
                print(f"warning: {key} dropped from {old} to {value}; keeping {old}",
                      file=sys.stderr)
                continue
            snap["values"][key] = value
            snap["as_of"][key] = today
        print(f"ok: {name}")


# --------------------------------------------------------------------------
# SVG helpers
# --------------------------------------------------------------------------

def txt(x: float, y: float, s: str, cls: str, anchor: str = "start",
        extra: str = "") -> str:
    anchor_attr = "" if anchor == "start" else f' text-anchor="{anchor}"'
    return (f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}"{anchor_attr}{extra}>'
            f'{escape(s)}</text>')


def base_css(t: Dict[str, str]) -> str:
    return f"""
    .sans {{ font-family: {SANS}; }}
    .mono {{ font-family: {MONO}; }}
    .ink {{ fill: {t['text']}; }}
    .muted {{ fill: {t['muted']}; }}
    """


def svg_doc(width: int, height: int, title: str, desc: str, body: str) -> str:
    doc = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
           f'height="{height}" viewBox="0 0 {width} {height}" role="img" '
           f'aria-labelledby="title desc">'
           f'<title id="title">{escape(title)}</title>'
           f'<desc id="desc">{escape(desc)}</desc>{body}</svg>\n')
    ET.fromstring(doc)  # fail loudly on malformed markup before writing
    return doc


def panel(width: int, height: int, t: Dict[str, str]) -> str:
    return (f'<rect x="1" y="1" width="{width - 2}" height="{height - 2}" '
            f'rx="22" fill="{t["panel"]}" stroke="{t["border"]}" '
            f'stroke-width="1.5"/>')


def logo_defs(t: Dict[str, str], prefix: str, cx: float, cy: float,
              rx: float, ry: float) -> str:
    """Gradients and the overlap clip for the CrossGL mark."""
    stops = ((0, ".32"), (.24, ".96"), (.5, ".9"), (.76, ".96"), (1, ".32"))
    out = []
    for name, colour in (("c", t["logo_coral"]), ("b", t["logo_cyan"])):
        stop_tags = "".join(
            f'<stop offset="{o}" stop-color="{colour}" stop-opacity="{a}"/>'
            for o, a in stops)
        out.append(f'<linearGradient id="{prefix}{name}" x1="0" y1="0" '
                   f'x2="1" y2="0">{stop_tags}</linearGradient>')
    # Where the ellipses cross, the real mark runs red to indigo to blue from
    # left to right. Painting that region explicitly avoids blend modes,
    # which turn red over cyan into a muddy green.
    left, mid, right = t["overlap"].split(",")
    out.append(
        f'<linearGradient id="{prefix}o" gradientUnits="userSpaceOnUse" '
        f'x1="{cx - 22}" y1="{cy}" x2="{cx + 22}" y2="{cy}">'
        f'<stop offset="0" stop-color="{left}"/>'
        f'<stop offset=".5" stop-color="{mid}"/>'
        f'<stop offset="1" stop-color="{right}"/></linearGradient>')
    out.append(
        f'<clipPath id="{prefix}x"><ellipse cx="{cx}" cy="{cy}" rx="{rx}" '
        f'ry="{ry}" transform="rotate(45 {cx} {cy})"/></clipPath>')
    return "".join(out)


def logo(cx: float, cy: float, rx: float, ry: float, prefix: str) -> str:
    """Two crossed ellipses with a separately painted overlap."""
    blue = (f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" '
            f'transform="rotate(-45 {cx} {cy})"')
    return (f'{blue} fill="url(#{prefix}b)"/>'
            f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="url(#{prefix}c)" '
            f'transform="rotate(45 {cx} {cy})"/>'
            f'<g clip-path="url(#{prefix}x)">{blue} fill="url(#{prefix}o)" '
            f'fill-opacity=".97"/></g>')


# --------------------------------------------------------------------------
# Hero
# --------------------------------------------------------------------------

# Clockwise from the top right. Direction is what CrossTL offers for that
# language: "both" ways, output only, or input only (see the CrossTL README).
LANGUAGES = [
    ("Metal", "gfx", "both"), ("HLSL", "gfx", "both"), ("GLSL", "gfx", "both"),
    ("SPIR-V", "gfx", "both"), ("WGSL", "gfx", "out"), ("GLSL ES", "gfx", "out"),
    ("Slang", "gfx", "both"), ("Mojo", "sys", "both"), ("Rust", "sys", "both"),
    ("OpenCL", "gpu", "in"), ("HIP", "gpu", "both"), ("CUDA", "gpu", "both"),
]


def hero_svg(theme: str) -> str:
    t = THEMES[theme]
    W, H = 1000, 340
    cx, cy = 775.0, 170.0
    family = {"gfx": t["cyan"], "gpu": t["coral"], "sys": t["violet"]}

    css = [base_css(t), f"""
    .prompt {{ font-size: 17px; }}
    .dollar {{ fill: {t['emerald']}; }}
    .file {{ fill: {t['coral']}; }}
    .name {{ font-size: 58px; font-weight: 700; letter-spacing: -0.5px; }}
    .role {{ font-size: 23px; font-weight: 600; }}
    .sub {{ font-size: 18px; }}
    .status {{ font-size: 15px; fill: {t['emerald']}; }}
    .pill {{ font-size: 13.5px; }}
    .cap {{ font-size: 12.5px; }}
    .typed {{ animation: type 1.25s steps(47, end) 0.25s backwards; }}
    .cursor {{ animation: blink 1s steps(1, end) 1.5s 3 backwards; }}
    .hub {{ transform-box: fill-box; transform-origin: center;
            animation: pop 0.75s cubic-bezier(.2, .8, .2, 1) 0.35s backwards; }}
    .spoke {{ stroke-dasharray: 1; stroke-dashoffset: 0;
              animation: draw 0.45s ease-out backwards; }}
    .tag {{ animation: rise 0.35s ease-out backwards; }}
    .dot {{ opacity: 0; }}
    .done {{ animation: rise 0.5s ease-out 2.6s backwards; }}
    @keyframes type {{ from {{ clip-path: inset(0 100% 0 0); }}
                       to {{ clip-path: inset(0 0 0 0); }} }}
    @keyframes blink {{ 0% {{ opacity: 0; }} 50% {{ opacity: 1; }} }}
    @keyframes pop {{ from {{ opacity: 0; transform: scale(.55) rotate(-35deg); }} }}
    @keyframes draw {{ from {{ stroke-dashoffset: 1; }} }}
    @keyframes rise {{ from {{ opacity: 0; transform: translateY(5px); }} }}
    """]

    spokes, tags, dots = [], [], []
    for k, (label, fam, direction) in enumerate(LANGUAGES):
        theta = math.radians(-75 + 30 * k)
        c, s = math.cos(theta), math.sin(theta)
        x0, y0 = cx + 64 * c, cy + 76 * s      # clear of the hub and its caption
        x1, y1 = cx + 172 * c, cy + 126 * s     # pill centre
        colour = family[fam]
        delay = 0.9 + 0.07 * k
        spokes.append(
            f'<path class="spoke" d="M{x0:.1f} {y0:.1f}L{x1:.1f} {y1:.1f}" '
            f'pathLength="1" stroke="{colour}" stroke-opacity="{t["spoke"]}" '
            f'stroke-width="1.6" fill="none" style="animation-delay:{delay:.2f}s"/>')
        w = len(label) * 8.2 + 22
        tags.append(
            f'<g class="tag" style="animation-delay:{delay + 0.25:.2f}s">'
            f'<rect x="{x1 - w / 2:.1f}" y="{y1 - 14:.1f}" width="{w:.1f}" '
            f'height="28" rx="14" fill="{t["surface"]}" stroke="{colour}" '
            f'stroke-opacity="0.75" stroke-width="1.3"/>'
            + txt(x1, y1 + 4.6, label, "mono pill ink", "middle") + '</g>')
        # One packet per spoke, flowing the way CrossTL translates.
        sx, sy, ex, ey = (x1, y1, x0, y0) if direction == "in" else (x0, y0, x1, y1)
        name = f"m{k}"
        css.append(
            f"@keyframes {name} {{ 0% {{ opacity: 0; transform: translate(0, 0); }}"
            f" 15% {{ opacity: 1; }} 85% {{ opacity: 1; }}"
            f" 100% {{ opacity: 0; transform: translate({ex - sx:.1f}px, {ey - sy:.1f}px); }} }}")
        dots.append(
            f'<circle class="dot" cx="{sx:.1f}" cy="{sy:.1f}" r="3.2" fill="{colour}" '
            f'style="animation:{name} 0.8s ease-in-out {delay + 0.45:.2f}s 1"/>')

    css.append("""
    @media (prefers-reduced-motion: reduce) {
      * { animation: none !important; }
    }
    """)

    defs = (
        "<defs>"
        + logo_defs(t, "h", cx, cy, 62, 21)
        + f'<radialGradient id="gc"><stop offset="0" stop-color="{t["logo_coral"]}" '
          f'stop-opacity="{t["glow"]}"/><stop offset="1" stop-color="{t["logo_coral"]}" '
          'stop-opacity="0"/></radialGradient>'
        + f'<radialGradient id="gb"><stop offset="0" stop-color="{t["logo_cyan"]}" '
          f'stop-opacity="{t["glow"]}"/><stop offset="1" stop-color="{t["logo_cyan"]}" '
          'stop-opacity="0"/></radialGradient>'
        + f"<style>{''.join(css)}</style></defs>"
    )

    left_x = 48
    # The clip runs over the text's own box, so the reveal keeps pace with
    # whatever monospace font the visitor has, one character per step.
    prompt = (
        f'<text x="{left_x}" y="70" class="mono prompt ink typed">'
        '<tspan class="dollar">$</tspan> crosstl translate '
        '<tspan class="file">nripesh.cgl</tspan> --backend human</text>'
        f'<text x="{left_x}" y="70" class="mono prompt ink" aria-hidden="true">'
        '<tspan fill-opacity="0">$ crosstl translate nripesh.cgl --backend human'
        '</tspan><tspan class="cursor"> \u258c</tspan></text>'
    )
    identity = (
        txt(left_x, 150, "Nripesh Niketan", "sans name ink")
        + txt(left_x, 192, "Compiler & ML systems engineer", "sans role ink")
        + txt(left_x, 225, "Founder of CrossGL \u00b7 Sheffield, UK", "sans sub muted")
        + f'<g class="done">'
        + txt(left_x, 292, "\u2713 wrote nripesh.human \u00b7 0 errors \u00b7 "
              "warnings suppressed", "mono status")
        + "</g>"
    )
    hub = (
        f'<circle cx="{cx - 30}" cy="{cy - 20}" r="160" fill="url(#gc)"/>'
        f'<circle cx="{cx + 30}" cy="{cy + 20}" r="160" fill="url(#gb)"/>'
        # Packets sit under the pills, so they slide in behind each label.
        + "".join(spokes) + "".join(dots) + "".join(tags)
        + f'<g class="hub">{logo(cx, cy, 62, 21, "h")}</g>'
        + txt(cx, cy + 64, "CrossGL IR", "mono cap muted", "middle")
    )
    body = defs + panel(W, H, t) + prompt + identity + hub
    return svg_doc(
        W, H, "Nripesh Niketan",
        "A terminal prompt runs crosstl translate nripesh.cgl --backend human "
        "and prints Nripesh Niketan, compiler and ML systems engineer, founder "
        "of CrossGL, Sheffield, UK. Beside it the CrossGL logo is the hub of a "
        "diagram with spokes to twelve GPU languages.", body)


# --------------------------------------------------------------------------
# Stats card
# --------------------------------------------------------------------------

def stats_svg(theme: str, v: dict, as_of: str) -> str:
    t = THEMES[theme]
    W, H = 1000, 188
    css = base_css(t) + """
    .head { font-size: 13px; }
    .big { font-size: 38px; font-weight: 700; }
    .label { font-size: 15px; font-weight: 600; }
    .foot { font-size: 12.5px; }
    .footmono { font-size: 12px; }
    """
    tiles = [
        (f"{v['crosstl_stars']:,}", "stars on CrossTL", "thank you, strangers",
         t["coral"], "sans"),
        (f"{v['upstream_prs']:,}", "PRs merged upstream",
         f"into {v['upstream_repos']} other people's repos", t["cyan"], "sans"),
        (f"{v['contributions_year']:,}", "contributions (1 yr)",
         "some were formatter bumps", t["violet"], "sans"),
        (f"v{v['crosstl_version']}", "crosstl on PyPI", "pip install crosstl",
         t["emerald"], "mono"),
    ]
    parts = [panel(W, H, t),
             txt(32, 34, "$ nripesh --stats", "mono head muted"),
             txt(W - 32, 34, f"refreshed weekly \u00b7 data as of {as_of}",
                 "sans head muted", "end")]
    tile_w, gap, y = 222, 16, 52
    for i, (big, label, foot, colour, foot_font) in enumerate(tiles):
        x = 32 + i * (tile_w + gap)
        parts.append(f'<rect x="{x}" y="{y}" width="{tile_w}" height="112" rx="14" '
                     f'fill="{t["surface"]}" stroke="{t["border"]}"/>')
        parts.append(txt(x + 18, y + 48, big, "sans big", extra=f' fill="{colour}"'))
        parts.append(txt(x + 18, y + 74, label, "sans label ink"))
        foot_cls = "mono footmono muted" if foot_font == "mono" else "sans foot muted"
        parts.append(txt(x + 18, y + 96, foot, foot_cls))
    desc = (f"{v['crosstl_stars']:,} GitHub stars on CrossTL; "
            f"{v['upstream_prs']:,} pull requests merged into "
            f"{v['upstream_repos']} other people's repositories; "
            f"{v['contributions_year']:,} contributions in the last year; "
            f"crosstl v{v['crosstl_version']} on PyPI. Data as of {as_of}.")
    return svg_doc(W, H, "Nripesh by the numbers", desc,
                   f"<defs><style>{css}</style></defs>" + "".join(parts))


# --------------------------------------------------------------------------
# Chess card
# --------------------------------------------------------------------------

def expected_score(me: int, engine: int) -> float:
    return 1.0 / (1.0 + 10 ** ((engine - me) / 400.0))


def percent(p: float) -> str:
    pct = p * 100
    if pct < 0.01:
        return "under 0.01%"
    if pct < 10:
        return f"{pct:.2f}%"
    return f"{pct:.0f}%"


def chess_svg(theme: str, v: dict, as_of: str) -> str:
    t = THEMES[theme]
    W, H = 1000, 300
    me, bot = int(v["me_bullet"]), int(v["bot_bullet"])
    games, draws = int(v["bot_games"]), int(v["bot_draws"])
    score = expected_score(me, bot)
    gap = bot - me
    draw_rate = round(100 * draws / games) if games else 0
    css = base_css(t) + """
    .head { font-size: 13px; }
    .kicker { font-size: 12px; letter-spacing: 1.8px; font-weight: 600; }
    .who { font-size: 30px; font-weight: 700; }
    .line { font-size: 16px; }
    .note { font-size: 14px; }
    .vs { font-size: 16px; font-weight: 600; }
    .eval { font-size: 13px; font-weight: 700; }
    .cap { font-size: 15px; }
    """
    defs = (f'<defs><linearGradient id="bar" x1="0" y1="0" x2="1" y2="0">'
            f'<stop offset="0" stop-color="{t["coral"]}"/>'
            f'<stop offset=".55" stop-color="{t["violet"]}"/>'
            f'<stop offset="1" stop-color="{t["cyan"]}"/></linearGradient>'
            f"<style>{css}</style></defs>")

    bar_x, bar_w, bar_y, bar_h = 40.0, 920.0, 190.0, 22.0
    me_w = max(9.0, min(bar_w - 9.0, score * bar_w))
    if gap > 0:
        eval_label = f"+{gap / 100:.1f}"
        caption = (f"Expected score against my own engine: {percent(score)}. "
                   "The bar is being generous.")
    else:
        eval_label = f"{gap / 100:.1f}"
        caption = "I'm ahead of my own engine. Somebody check the API."
    parts = [
        panel(W, H, t),
        txt(32, 34, "$ ./versus --me --my-engine", "mono head muted"),
        txt(W - 32, 34, f"Lichess + FIDE \u00b7 data as of {as_of}",
            "sans head muted", "end"),
        # The human
        txt(40, 74, "THE HUMAN", "sans kicker muted"),
        txt(40, 110, "Nripesh", "sans who ink"),
        txt(40, 138, f"FIDE {v['fide_standard']} \u00b7 Lichess bullet {me}",
            "sans line ink"),
        txt(40, 162, "plays for Sheffield University", "sans note muted"),
        # The engine
        txt(960, 74, "THE ENGINE", "sans kicker muted", "end"),
        txt(960, 110, "MetalFish", "sans who ink", "end"),
        txt(960, 138, f"Lichess bullet {bot} \u00b7 BOT", "sans line ink", "end"),
        txt(960, 162, f"{games:,} games, {draw_rate}% drawn. A pacifist.",
            "sans note muted", "end"),
        # vs
        f'<circle cx="500" cy="112" r="24" fill="{t["surface"]}" '
        f'stroke="{t["border"]}" stroke-width="1.5"/>',
        txt(500, 117.5, "vs", "sans vs muted", "middle"),
        # The evaluation bar: my share of the expected score, then the engine's.
        f'<rect x="{bar_x}" y="{bar_y}" width="{bar_w}" height="{bar_h}" rx="11" '
        f'fill="url(#bar)"/>',
        f'<path d="M{bar_x + 11} {bar_y}H{bar_x + me_w}V{bar_y + bar_h}'
        f'H{bar_x + 11}A11 11 0 0 1 {bar_x + 11} {bar_y}Z" fill="{t["text"]}"/>',
        txt(bar_x + bar_w - 12, bar_y + 15.5, eval_label, "mono eval",
            "end", extra=f' fill="{t["bar_text"]}"'),
        txt(bar_x, 232, "me", "sans note muted"),
        txt(bar_x + bar_w, 232, "my engine", "sans note muted", "end"),
        txt(500, 270, caption, "sans cap ink", "middle"),
    ]
    desc = (f"Me: FIDE {v['fide_standard']}, Lichess bullet {me}. My engine "
            f"MetalFish: Lichess bullet {bot}, {games:,} games, {draw_rate}% drawn. "
            f"By the Elo formula my expected score against it is {percent(score)}. "
            f"Data as of {as_of}.")
    return svg_doc(W, H, "Me against my own chess engine", desc,
                   defs + "".join(parts))


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

STATS_KEYS = ("crosstl_stars", "upstream_prs", "upstream_repos",
              "contributions_year", "crosstl_version")
CHESS_KEYS = ("fide_standard", "me_bullet", "bot_bullet", "bot_games", "bot_draws")


def oldest(snap: dict, keys: Tuple[str, ...]) -> str:
    return min(snap["as_of"][k] for k in keys)


# Each card links somewhere useful. The link also matters for rendering: an
# unlinked image gets GitHub's own link wrapped around it, which lands between
# <source> and <img> and can stop the dark variant from being picked.
CARD_LINKS = {
    "stats": ("https://github.com/search?q=author%3ANripeshN+is%3Apr+is%3Amerged"
              "+-user%3ACrossGL+-user%3ANripeshN&type=pullrequests"),
    "chess": "https://lichess.org/@/Nripesh-MetalFish",
}


def picture(name: str, alt: str) -> str:
    alt_attr = escape(alt, {'"': "&quot;"})
    href = escape(CARD_LINKS[name], {'"': "&quot;"})
    return (f'<a href="{href}">\n'
            "  <picture>\n"
            f'    <source media="(prefers-color-scheme: dark)" srcset="assets/{name}-dark.svg">\n'
            f'    <img src="assets/{name}-light.svg" width="100%" alt="{alt_attr}">\n'
            "  </picture>\n"
            "</a>")


def card_alts(snap: dict) -> Dict[str, str]:
    """Alt text for the README, so screen readers get the live numbers too."""
    v = snap["values"]
    score = percent(expected_score(int(v["me_bullet"]), int(v["bot_bullet"])))
    draw_rate = round(100 * v["bot_draws"] / v["bot_games"]) if v["bot_games"] else 0
    return {
        "stats": (f"{v['crosstl_stars']:,} stars on CrossTL. {v['upstream_prs']} "
                  f"pull requests merged into {v['upstream_repos']} other people's "
                  f"repositories. {v['contributions_year']:,} contributions in the "
                  f"last year. crosstl v{v['crosstl_version']} on PyPI. Data as of "
                  f"{oldest(snap, STATS_KEYS)}."),
        "chess": (f"Me against my own chess engine. Me: FIDE {v['fide_standard']}, "
                  f"Lichess bullet {v['me_bullet']}. MetalFish: Lichess bullet "
                  f"{v['bot_bullet']}, {v['bot_games']:,} games, {draw_rate}% drawn. "
                  f"My expected score against it is {score}. Data as of "
                  f"{oldest(snap, CHESS_KEYS)}."),
    }


def update_readme(snap: dict) -> None:
    """Rewrite the card blocks between their markers in README.md."""
    if not README.exists():
        return
    text = README.read_text(encoding="utf-8")
    for name, alt in card_alts(snap).items():
        start, end = f"<!-- {name}-card:start -->", f"<!-- {name}-card:end -->"
        if text.count(start) != 1 or text.count(end) != 1:
            print(f"warning: README markers for the {name} card not found exactly once",
                  file=sys.stderr)
            continue
        head, rest = text.split(start)
        _, tail = rest.split(end)
        text = f"{head}{start}\n{picture(name, alt)}\n{end}{tail}"
    changed = write_if_changed(README, text)
    print(f"{'wrote' if changed else 'same '} README.md card blocks")


def write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return True


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    snap = load_snapshot()
    refresh(snap, token, today)

    missing = [k for k in STATS_KEYS + CHESS_KEYS if k not in snap["values"]]
    if missing:
        print(f"error: no value yet for {', '.join(missing)}", file=sys.stderr)
        return 1

    values = snap["values"]
    outputs = {}
    for theme in THEMES:
        outputs[f"hero-{theme}.svg"] = hero_svg(theme)
        outputs[f"stats-{theme}.svg"] = stats_svg(theme, values, oldest(snap, STATS_KEYS))
        outputs[f"chess-{theme}.svg"] = chess_svg(theme, values, oldest(snap, CHESS_KEYS))

    for name, svg in outputs.items():
        changed = write_if_changed(ASSETS / name, svg)
        print(f"{'wrote' if changed else 'same '} assets/{name} ({len(svg):,} bytes)")

    snap["values"] = dict(sorted(values.items()))
    snap["as_of"] = dict(sorted(snap["as_of"].items()))
    write_if_changed(SNAPSHOT, json.dumps(snap, indent=2) + "\n")
    update_readme(snap)
    return 0


if __name__ == "__main__":
    sys.exit(main())
