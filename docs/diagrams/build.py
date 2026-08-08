"""Draws the README diagrams as SVG.

The diagrams use the application's own tokens: the palette from static/style.css,
Space Grotesk for display type, cards on a warm graphite plate. The font is
subsetted to the characters these diagrams use and embedded as a data URI, so a
diagram carries its own typeface instead of depending on the reader's machine.

SpaceGrotesk-subset.woff2 is derived from app/static/fonts/SpaceGrotesk.woff2 and
carries the same licence, the SIL Open Font License 1.1. The licence and copyright
notice are in app/static/fonts/OFL.txt. The font declares no Reserved Font Name, so
the subset keeps the original family name.

    python docs/diagrams/build.py
"""

from __future__ import annotations

import base64
import itertools
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE.parent / "img"

# ---------- tokens (mirrors app/static/style.css) ----------

BG = "#131419"
SURFACE = "#1f2126"
RAISED = "#24262c"
BORDER = "#31353e"
BORDER_STRONG = "#454a54"
TEXT = "#e9e6e0"
MUTED = "#98948b"
ACCENT = "#e8a13d"
DANGER = "#e5484d"
OK = "#6fbf73"

# Two lane fills a shade apart: the manual one cools off toward grey, the EPS one
# keeps a little of the brand warmth. Same lightness, so neither reads as louder.
LANE_COLD = "#1c1e23"
LANE_COLD_EDGE = "#2f333c"
LANE_WARM = "#221f1c"
LANE_WARM_EDGE = "#3a3429"


def _font_face() -> str:
    b64 = base64.b64encode((HERE / "SpaceGrotesk-subset.woff2").read_bytes()).decode()
    return (
        "@font-face{font-family:'Space Grotesk';font-weight:300 700;"
        f"src:url(data:font/woff2;base64,{b64}) format('woff2')}}"
    )


# Shared type scale. `.k` is the uppercase letterspaced label the app puts at the
# top of every card; `.n` is body text inside a node.
STYLE = """
text{dominant-baseline:central;font-family:system-ui,'Segoe UI',sans-serif}
.d{font-family:'Space Grotesk',system-ui,sans-serif}
.k{font-family:'Space Grotesk',system-ui,sans-serif;font-size:13px;font-weight:600;
   letter-spacing:1.8px;text-transform:uppercase}
.lab{font-family:'Space Grotesk',system-ui,sans-serif;font-size:11px;font-weight:600;
     letter-spacing:1.3px;text-transform:uppercase}
.n{font-size:15px}
.cap{font-size:14px;fill:#98948b}
"""


def svg(width: int, height: int, title: str, desc: str, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="ti de">'
        f'<title id="ti">{title}</title><desc id="de">{desc}</desc>'
        f"<style>{_font_face()}{STYLE}</style>"
        # The marker is anchored at its base (refX=0) and sized in user units, so a
        # line stops HEAD px short of its target and the head draws the rest. Anchoring
        # at the tip instead lets the stem poke through the point, which is the notch
        # that showed up in the first draft.
        '<defs><marker id="ar" viewBox="0 0 10 10" refX="0" refY="5" markerWidth="10" '
        'markerHeight="10" markerUnits="userSpaceOnUse" orient="auto-start-reverse">'
        '<path d="M0 1.5L9 5L0 8.5z" fill="context-stroke"/></marker></defs>'
        f'<rect width="{width}" height="{height}" rx="14" fill="{BG}"/>'
        f"{body}</svg>"
    )


def card(x, y, w, h, *, fill=SURFACE, stroke=BORDER, r=12, sw=1) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
    )


_clip = itertools.count()


def left_bar(x, y, w_box, h, tone, w=6, r=8) -> str:
    """The coloured left edge the app puts on a vital or overdue row. Clipped to the
    box so its corners follow the same radius, which is what a CSS border does."""
    cid = f"c{next(_clip)}"
    return (
        f'<clipPath id="{cid}"><rect x="{x}" y="{y}" width="{w_box}" height="{h}" rx="{r}"/></clipPath>'
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{tone}" clip-path="url(#{cid})"/>'
    )


def node(x, y, w, h, label, *, tone=None) -> str:
    """A box on the flow. `tone` tints it with a semantic colour, the way the app
    tints an overdue block red and a completed control green."""
    out = []
    if tone:
        out.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{tone}" fill-opacity="0.1" '
            f'stroke="{tone}" stroke-opacity="0.55"/>'
        )
        out.append(left_bar(x, y, w, h, tone))
    else:
        out.append(card(x, y, w, h, fill=RAISED, stroke=BORDER_STRONG, r=8))
    out.append(
        f'<text class="n" x="{x + w / 2}" y="{y + h / 2}" text-anchor="middle" fill="{TEXT}">{label}</text>'
    )
    return "".join(out)


HEAD = 9  # length the marker adds past the end of a line


def down(x, y1, y2, colour=MUTED) -> str:
    """A vertical arrow whose point lands on y2."""
    return (
        f'<line x1="{x}" y1="{y1}" x2="{x}" y2="{y2 - HEAD}" stroke="{colour}" '
        f'stroke-width="1.8" marker-end="url(#ar)"/>'
    )


def curve(d, colour=MUTED, opacity=1) -> str:
    """An arrowed path. `d` must end HEAD px short of the target, along the tangent."""
    return (
        f'<path d="{d}" fill="none" stroke="{colour}" stroke-opacity="{opacity}" '
        f'stroke-width="1.8" marker-end="url(#ar)"/>'
    )


# ---------- 1. the maintenance loop ----------


def maintenance_loop() -> str:
    b = []

    # the shared starting point
    b.append(card(370, 22, 260, 48, fill=RAISED, stroke=BORDER_STRONG, r=24))
    b.append(
        f'<text class="d" x="500" y="46" text-anchor="middle" font-size="16" font-weight="500" fill="{TEXT}">A task occurs to you</text>'
    )

    # lane titles sit above their lanes so nothing crosses them
    # each title hugs the outer edge of its own lane, so the two mirror each other
    b.append(f'<text class="k" x="40" y="106" fill="{MUTED}">Ordinary to-do list</text>')
    b.append(f'<text class="k" x="960" y="106" text-anchor="end" fill="{ACCENT}">EPS</text>')

    b.append(card(40, 126, 440, 420, fill=LANE_COLD, stroke=LANE_COLD_EDGE))
    b.append(card(520, 126, 440, 326, fill=LANE_WARM, stroke=LANE_WARM_EDGE))

    # the task forks into the two lanes; drawn after the lanes so it reads as
    # crossing into them rather than stopping at the border
    b.append(curve("M452,70 C398,100 234,96 230,151"))
    b.append(curve("M548,70 C602,100 736,96 740,151"))

    # left lane: four steps that close into a loop
    b.append(node(90, 170, 280, 52, "Write it down"))
    b.append(down(230, 222, 264))
    b.append(node(90, 264, 280, 52, "Build tomorrow&#8217;s list"))
    b.append(down(230, 316, 358))
    b.append(node(90, 358, 280, 52, "Work on tasks"))
    b.append(down(230, 410, 452))
    b.append(node(90, 452, 280, 52, "Missed and new tasks pile up", tone=DANGER))

    # the return leg is the whole point: the work comes back to you every day
    b.append(curve("M370,478 H408 Q428,478 428,458 V310 Q428,290 408,290 H379", DANGER, 0.75))
    b.append(
        f'<text class="lab" x="448" y="384" text-anchor="middle" fill="{DANGER}" '
        f'transform="rotate(-90 448 384)">Daily maintenance</text>'
    )

    # right lane: three steps and no return leg
    b.append(node(600, 170, 280, 52, "Enter it into the EPS"))
    b.append(down(740, 222, 264))
    b.append(node(600, 264, 280, 52, "The EPS builds the list each day"))
    b.append(down(740, 316, 358))
    b.append(node(600, 358, 280, 52, "Do the work", tone=OK))

    # the conclusion, in the brand colour, carrying no label of its own
    b.append(
        f'<rect x="520" y="466" width="440" height="80" rx="12" fill="{ACCENT}" fill-opacity="0.06" '
        f'stroke="{ACCENT}" stroke-opacity="0.4"/>'
    )
    for i, line in enumerate(
        [
            "The EPS takes the maintenance off you, so you spend",
            "less time planning and more time doing.",
        ]
    ):
        b.append(
            f'<text x="740" y="{496 + i * 22}" text-anchor="middle" font-size="14.5" fill="{ACCENT}">{line}</text>'
        )

    return svg(
        1000,
        570,
        "A to-do list makes you rebuild it every day; EPS builds it for you",
        "Two lanes. On an ordinary to-do list you write a task down, build tomorrow's list, "
        "work on tasks, and whatever you missed piles up with the new tasks, which sends you "
        "back to building the list again the next day. In EPS you enter the task once, EPS "
        "builds the list each day, and you do the work. There is no return leg.",
        "".join(b),
    )


# ---------- 2. container topology ----------


def text_width(s, size, tracking=0.0) -> float:
    """Rough advance width. Good enough to centre a pill around its label."""
    return len(s) * (size * 0.56 + tracking)


def chip(cx, cy, label, *, fg=MUTED, bg=RAISED, edge=BORDER_STRONG, size=12.5) -> str:
    """The app's pill chip, used here to sit a port number on top of an edge."""
    w = text_width(label, size) + 22
    h = 22
    return (
        f'<rect x="{cx - w / 2:.1f}" y="{cy - h / 2}" width="{w:.1f}" height="{h}" rx="11" '
        f'fill="{bg}" stroke="{edge}"/>'
        f'<text x="{cx}" y="{cy}" text-anchor="middle" font-size="{size}" fill="{fg}">{label}</text>'
    )


def box(x, y, w, h, head, sub, *, bar=None) -> str:
    """A service box: name on top, what it is underneath."""
    cx, cy = x + w / 2, y + h / 2
    out = [card(x, y, w, h, fill=RAISED, stroke=BORDER_STRONG, r=8)]
    if bar:
        out.append(left_bar(x, y, w, h, bar))
    out.append(
        f'<text class="d" x="{cx}" y="{cy - 11}" text-anchor="middle" font-size="16" '
        f'font-weight="600" fill="{TEXT}">{head}</text>'
    )
    out.append(
        f'<text x="{cx}" y="{cy + 12}" text-anchor="middle" font-size="12.5" fill="{MUTED}">{sub}</text>'
    )
    return "".join(out)


def topology() -> str:
    b = []

    b.append(box(24, 178, 160, 64, "Your browser", "outside"))

    b.append(f'<text class="k" x="250" y="52" fill="{MUTED}">Private network</text>')
    b.append(card(250, 70, 726, 280))

    # nginx is the only way in, so it carries the brand accent on its edge
    b.append(box(290, 178, 200, 64, "nginx", "static files and proxy", bar=ACCENT))
    b.append(box(580, 110, 200, 64, "web", "Flask and gunicorn"))
    b.append(box(580, 246, 200, 64, "worker", "scheduled jobs"))

    # Postgres as a cylinder: the one thing here that keeps its contents
    b.append(f'<path d="M830,169 V251 A65,14 0 0 0 960,251 V169 Z" fill="{RAISED}"/>')
    b.append(
        f'<path d="M830,169 V251 A65,14 0 0 0 960,251 V169" fill="none" stroke="{BORDER_STRONG}"/>'
    )
    b.append(
        f'<ellipse cx="895" cy="169" rx="65" ry="14" fill="{RAISED}" stroke="{BORDER_STRONG}"/>'
    )
    b.append(
        f'<text class="d" x="895" y="203" text-anchor="middle" font-size="16" font-weight="600" fill="{TEXT}">Postgres</text>'
    )
    b.append(
        f'<text x="895" y="225" text-anchor="middle" font-size="12.5" fill="{MUTED}">named volume</text>'
    )

    # the only edge that crosses the network boundary
    b.append(curve("M184,210 H281", ACCENT))
    b.append(chip(222, 210, ":80", fg=ACCENT, bg=BG, edge=f"{ACCENT}"))

    b.append(curve("M490,210 C534,210 540,142 571,142"))
    b.append(chip(536, 176, ":8000"))

    b.append(curve("M780,142 C804,142 812,158 820,172"))
    b.append(curve("M780,278 C804,278 812,262 820,248"))

    return svg(
        1000,
        374,
        "The four containers and what talks to what",
        "A browser reaches nginx on port 80. nginx is the only container published outside "
        "the private network; it serves the static files and proxies everything else to the "
        "web container on port 8000. The worker container takes no inbound traffic at all. "
        "Both web and worker read and write Postgres, which keeps its files on a named volume.",
        "".join(b),
    )


# ---------- 3. the streak rebuilds itself ----------


def day_cell(x, y, w, h, value, note=None, *, tone=None) -> str:
    """One day on a streak timeline, shaped like a calendar cell."""
    cx = x + w / 2
    out = []
    if tone:
        out.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="{tone}" fill-opacity="0.1" '
            f'stroke="{tone}" stroke-opacity="0.6"/>'
        )
    else:
        out.append(card(x, y, w, h, fill=RAISED, stroke=BORDER_STRONG, r=8))
    colour = tone if tone else TEXT
    out.append(
        f'<text class="d" x="{cx}" y="{y + 36}" text-anchor="middle" font-size="30" '
        f'font-weight="600" fill="{colour}">{value}</text>'
    )
    out.append(
        f'<text x="{cx}" y="{y + 62}" text-anchor="middle" font-size="12" fill="{MUTED}">{note or "days"}</text>'
    )
    return "".join(out)


def hop(gap, y, colour=MUTED, broken=False) -> str:
    """The short arrow carrying one day into the next. A broken one is dashed, which
    is how a streak that lost a day reads at a glance."""
    x0 = 202 + gap * 144
    dash = ' stroke-dasharray="3 3"' if broken else ""
    return (
        f'<line x1="{x0}" y1="{y}" x2="{x0 + 11}" y2="{y}" stroke="{colour}" '
        f'stroke-width="1.8"{dash} marker-end="url(#ar)"/>'
    )


def streak_rebuild() -> str:
    b = []
    xs = [80 + i * 144 for i in range(6)]
    w, h = 120, 90
    missed = 3

    # both rows sit the same distance below their label
    top, bottom, gap_to_label = 72, 284, 14

    b.append(
        f'<text class="lab" x="80" y="{top - gap_to_label}" fill="{MUTED}">'
        f"If a mistake was made in the logging of a task on a past day</text>"
    )
    for i, (x, v) in enumerate(zip(xs, [0, 1, 2, 0, 1, 2])):
        tone = DANGER if i == missed else None
        b.append(day_cell(x, top, w, h, v, "missed" if i == missed else None, tone=tone))
    # the two hops either side of the missed day are the ones that snapped
    for gap in range(5):
        touches = gap in (missed - 1, missed)
        b.append(hop(gap, top + h / 2, DANGER if touches else MUTED, broken=touches))

    b.append(
        f'<text class="lab" x="80" y="{bottom - gap_to_label}" fill="{ACCENT}">'
        f"The EPS recalculates everything after it, autonomously</text>"
    )
    for i, (x, v) in enumerate(zip(xs, [0, 1, 2, 3, 4, 5])):
        tone = OK if i == missed else None
        b.append(day_cell(x, bottom, w, h, v, "fixed" if i == missed else None, tone=tone))
    for gap in range(5):
        touches = gap in (missed - 1, missed)
        b.append(hop(gap, bottom + h / 2, OK if touches else MUTED))

    # the edit lands on one day; everything after it is recomputed, not re-entered
    b.append(
        curve(
            "M920,117 H954 Q972,117 972,135 V212 Q972,230 954,230 H590 Q572,230 572,248 V275",
            ACCENT,
        )
    )
    b.append(
        f'<text class="lab" x="772" y="214" text-anchor="middle" fill="{ACCENT}">'
        f"You can go back and correct it</text>"
    )

    return svg(
        1000,
        404,
        "Correcting one past day rebuilds the whole streak",
        "A streak counting 0, 1, 2, then dropping back to 0 on a day that was logged "
        "incorrectly, then 1, 2, with the chain broken either side of that day. Correcting "
        "the day makes the EPS replay the history, and the same six days now count 0, 1, 2, "
        "3, 4, 5 with the chain intact. Only the one day was edited; every day after it was "
        "recalculated automatically.",
        "".join(b),
    )


# ---------- 4. the version roadmap ----------

STOPS = [
    (
        "v0.1",
        [
            "The four containers run under",
            "Compose, and every push is",
            "built, tested and scanned.",
        ],
        "Done",
        OK,
    ),
    (
        "v0.2",
        ["Prometheus and Grafana turn", "the telemetry into dashboards", "and alerts."],
        "Next",
        ACCENT,
    ),
    (
        "v0.3",
        [
            "The stack becomes Helm charts",
            "on a local cluster, load-tested",
            "until it autoscales.",
        ],
        "Planned",
        None,
    ),
    (
        "v0.4",
        ["A private machine becomes", "production, and ArgoCD deploys", "it straight from git."],
        "Planned",
        None,
    ),
    (
        "v0.5",
        [
            "Terraform creates that machine",
            "and Ansible configures it,",
            "rebuildable from nothing.",
        ],
        "Planned",
        None,
    ),
    (
        "v1.0",
        ["The same design on AWS: a", "hand-built VPC, RDS and IAM,", "used and then destroyed."],
        "Planned",
        None,
    ),
    (
        "v1.x",
        ["The cluster swapped for EKS,", "with keyless AWS access and", "managed secrets."],
        "Planned",
        None,
    ),
]


def roadmap() -> str:
    b = []
    # seven stops will not fit one row at a readable size, so the track snakes:
    # four stops left to right, a turn down the right edge, three stops right to left
    row1 = [130, 370, 610, 850]
    row2 = [790, 500, 210]
    bar1, bar2 = 120, 420
    here = 250  # between the finished rung and the one being built

    # the track reads as a status bar: green up to the rung that is finished, brand
    # colour for the one being built, plain border for everything still ahead
    b.append(f'<rect x="36" y="{bar1 - 5}" width="{row1[0] - 36}" height="10" rx="5" fill="{OK}"/>')
    b.append(
        f'<rect x="{row1[0]}" y="{bar1 - 5}" width="{here - row1[0]}" height="10" fill="{ACCENT}"/>'
    )
    b.append(f'<rect x="{here}" y="{bar1 - 5}" width="{920 - here}" height="10" fill="{BORDER}"/>')
    # the turn: out to the right edge, down, and back in to the second row
    b.append(
        f'<path d="M920,{bar1} Q964,{bar1} 964,{bar1 + 44} V{bar2 - 44} Q964,{bar2} 920,{bar2}" '
        f'fill="none" stroke="{BORDER}" stroke-width="10"/>'
    )
    b.append(f'<rect x="90" y="{bar2 - 5}" width="{920 - 90}" height="10" fill="{BORDER}"/>')
    b.append(f'<path d="M90,{bar2 - 13} L58,{bar2} L90,{bar2 + 13} Z" fill="{BORDER}"/>')

    # "we are here", pinned to the track just past v0.1
    b.append(
        f'<rect x="{here - 62}" y="56" width="124" height="30" rx="15" fill="{ACCENT}"/>'
        f'<path d="M{here - 7},86 L{here},96 L{here + 7},86 Z" fill="{ACCENT}"/>'
        f'<text class="d" x="{here}" y="71" text-anchor="middle" font-size="13.5" font-weight="600" '
        f'fill="{BG}">We are here</text>'
    )

    rows = [(row1, bar1, 160, STOPS[:4]), (row2, bar2, 460, STOPS[4:])]
    for centres, bar_y, card_y, stops in rows:
        for cx, (name, lines, status, tone) in zip(centres, stops):
            # the stop itself
            fill, edge = (tone, tone) if tone else (RAISED, BORDER_STRONG)
            b.append(
                f'<circle cx="{cx}" cy="{bar_y}" r="13" fill="{fill}" stroke="{BG}" stroke-width="4"/>'
            )
            b.append(
                f'<circle cx="{cx}" cy="{bar_y}" r="13" fill="none" stroke="{edge}" stroke-width="2"/>'
            )
            b.append(
                f'<line x1="{cx}" y1="{bar_y + 15}" x2="{cx}" y2="{card_y}" stroke="{BORDER}" stroke-width="2"/>'
            )

            b.append(card(cx - 84, card_y, 168, 162))
            b.append(
                f'<text class="d" x="{cx}" y="{card_y + 28}" text-anchor="middle" font-size="21" '
                f'font-weight="600" fill="{tone or TEXT}">{name}</text>'
            )
            for i, line in enumerate(lines):
                b.append(
                    f'<text x="{cx}" y="{card_y + 58 + i * 19}" text-anchor="middle" font-size="12.5" fill="{MUTED}">{line}</text>'
                )

            if tone:
                b.append(
                    chip(cx, card_y + 136, status, fg=tone, bg=f"{tone}1f", edge="none", size=12.5)
                )
            else:
                b.append(chip(cx, card_y + 136, status, size=12.5))

    return svg(
        1000,
        660,
        "The version roadmap, a track over two rows",
        "Seven versions on a track that snakes over two rows. v0.1, the four containers under "
        "Compose with CI, is done. v0.2, Prometheus and Grafana over the running stack, is next "
        "and is where the project stands. v0.3 moves the stack onto Kubernetes, v0.4 adds "
        "pull-based deployment with ArgoCD and a private production machine, v0.5 builds that "
        "machine from code with Terraform and Ansible, v1.0 lifts the design onto AWS, and "
        "v1.x swaps the cluster for EKS.",
        "".join(b),
    )


# ---------- 5. the banner ----------

STACK = [
    "Python",
    "Flask",
    "HTMX",
    "Postgres",
    "Docker Compose",
    "Ansible",
    "Kubernetes",
    "Terraform",
    "AWS",
]


def banner() -> str:
    b = []

    # the same plate the dashboard opens with: kicker, wordmark, one line under it
    b.append(
        f'<text class="k" x="64" y="76" font-size="14" fill="{ACCENT}">Productivity system</text>'
    )
    b.append(
        f'<text class="d" x="60" y="134" font-size="76" font-weight="600" letter-spacing="-1.5" '
        f'fill="{TEXT}">EPS</text>'
    )
    b.append(
        f'<text x="64" y="192" font-size="17" fill="{MUTED}">A productivity webapp that assembles your day for you.</text>'
    )

    # an agenda, abstracted: one overdue at the top, one vital, one already done
    rows = [DANGER, None, ACCENT, None, OK]
    for i, tone in enumerate(rows):
        y = 52 + i * 36
        b.append(card(640, y, 300, 26, fill=RAISED, stroke=BORDER, r=6))
        if tone:
            b.append(left_bar(640, y, 300, 26, tone, w=5, r=6))
        done = tone == OK
        mark = OK if done else BORDER_STRONG
        b.append(
            f'<rect x="656" y="{y + 7}" width="12" height="12" rx="3" fill="{OK if done else "none"}" '
            f'fill-opacity="{0.16 if done else 1}" stroke="{mark}" stroke-width="1.5"/>'
        )
        bar_w = [150, 196, 168, 210, 132][i]
        b.append(
            f'<rect x="680" y="{y + 11}" width="{bar_w}" height="5" rx="2.5" fill="{MUTED}" '
            f'fill-opacity="{0.28 if done else 0.5}"/>'
        )
        if done:  # finished work sinks to the bottom, struck through
            b.append(
                f'<line x1="678" y1="{y + 13.5}" x2="{682 + bar_w}" y2="{y + 13.5}" stroke="{MUTED}" stroke-width="1.2"/>'
            )
        if tone == DANGER:
            b.append(
                f'<rect x="886" y="{y + 8}" width="40" height="10" rx="5" fill="{DANGER}" fill-opacity="0.6"/>'
            )

    x = 64
    for name in STACK:
        w = text_width(name, 13) + 24
        b.append(
            f'<rect x="{x:.1f}" y="249" width="{w:.1f}" height="26" rx="13" fill="{RAISED}" stroke="{BORDER_STRONG}"/>'
            f'<text x="{x + w / 2:.1f}" y="262" text-anchor="middle" font-size="13" fill="{MUTED}">{name}</text>'
        )
        x += w + 8

    return svg(
        1000,
        320,
        "EPS",
        "The EPS wordmark over its one-line description, with the stack listed underneath: "
        "Python, Flask, HTMX, Postgres, Docker Compose, Ansible, Kubernetes, Terraform and AWS.",
        "".join(b),
    )


DIAGRAMS = {
    "banner.svg": banner,
    "maintenance-loop.svg": maintenance_loop,
    "topology.svg": topology,
    "streak-rebuild.svg": streak_rebuild,
    "roadmap.svg": roadmap,
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in DIAGRAMS.items():
        (OUT / name).write_text(fn(), encoding="utf-8")
        print(f"wrote docs/img/{name}")


if __name__ == "__main__":
    main()
