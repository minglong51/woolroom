"""Render a static visual review board for a content pack.

Validates the pack through the standalone contract (every gate runs; a
refused pack fails here before any human looks at it), then emits ONE
self-contained HTML file: per species, every coat × the pose/eye-state
classes the wool rig drives. The board inlines the stylesheet bundled with
Woolpack and applies each coat through the ``--dog-body``, ``--dog-belly``,
and ``--dog-point`` palette variables. A second section overlays the pack's
hitbox geometry on the figure so zone thresholds can be reviewed against the
art they gate.

No server, no JS build — the board opens anywhere:

    woolpack render path/to/pack [-o /tmp/pack-board.html]

Deliberate shortcuts (it is a review board, not a product page):
- every figure cell repeats `id="wool-scene"` — invalid HTML, but the bundled
  figure selectors are id-scoped and CSS matches them all; there is no JS here
  to trip over the duplicates;
- the wall/floor gradients are approximated with their flat :root
  wool tones — enough to judge coat contrast, not a pixel-double;
- the JS-driven wrappers (#dogmove/#dog-gait/scale transform) are identity
  for an adult at rest and are omitted; .squishg > .breath are real.
"""

from __future__ import annotations

import argparse
import html
import json
from importlib.resources import files
from pathlib import Path

from woolpack.validation import validate_pack

STYLE_CSS = files("woolpack.resources").joinpath("style.css")

# The pose/eye-state classes applied to #wool-scene. The bundled stylesheet
# pins the eye-group opacity flips; "base" is the untouched resting figure.
POSES: list[tuple[str, str]] = [
    ("base", ""),
    ("happy", "happy"),
    ("side-eye", "sideeye"),
    ("sleeping", "sleeping"),
    ("one-eye nap", "sleeping pose-oneeye"),
]

BOARD_CSS = """
body { background: var(--bg); color: var(--ink); font: 14px/1.45 var(--sans);
       margin: 0; padding: 28px 32px 44px; }
h1 { font: 26px/1.2 var(--serif); margin: 0 0 4px; }
h2 { font: 18px/1.2 var(--serif); margin: 30px 0 4px; }
.meta { color: var(--muted); margin: 0 0 6px; }
.meta code, .cell figcaption code { font-family: ui-monospace, monospace; font-size: 12px; }
.row { display: flex; gap: 14px; flex-wrap: wrap; margin: 10px 0 4px; }
.cell { margin: 0; }
.cell figcaption { text-align: center; color: var(--muted); font-size: 12px;
                   padding-top: 6px; }
.scene { border-radius: 14px; overflow: hidden; border: 1px solid var(--line); }
/* Deliberately repeated id: the bundled figure selectors are id-scoped, and
   CSS matches every element carrying it without any JS. */
.scene #wool-scene { width: 200px; }
.scene.small #wool-scene { width: 60px; }
.scene.hitbox #wool-scene { width: 260px; }
.wool-scene-box { width: 100%; display: block; }
svg text { font-family: ui-monospace, monospace; }
"""


def _figure_cell(species_id: str, figure: str, coat_vars: str, pose_class: str,
                 label: str, variant: str = "room") -> str:
    """One rendered figure: the room's #wool-scene wrapper (data-species,
    data-time, pose classes), the pack coat as inline palette vars (the
    figureCoatStyle mechanism), and the index.html injection stack
    (.squishg > .breath > figure) inside the room's viewBox. `variant` is a
    sizing hook for the board's own CSS: room (~room scale), small (the 60px
    readability check), hitbox (bigger, for the zone overlay)."""
    cell_class = "cell scene" if variant == "room" else f"cell scene {variant}"
    return f"""<figure class="{cell_class}">
<div id="wool-scene" data-species="{html.escape(species_id)}" data-time="day" class="{html.escape(pose_class)}"
     style="{html.escape(coat_vars)}">
<svg class="wool-scene-box" viewBox="0 0 400 520" role="img" aria-label="{html.escape(label)}">
  <rect width="400" height="336" fill="#ded2bd"/>
  <rect y="336" width="400" height="184" fill="#b3a58e"/>
  <g class="squishg"><g class="breath">
{figure}
  </g></g>
</svg>
</div>
<figcaption>{html.escape(label)}</figcaption>
</figure>"""


def _hitbox_cell(species_id: str, figure: str, coat_vars: str, geometry: dict,
                 coat_id: str) -> str:
    """The figure with its touch geometry drawn over it, clipped to the real
    #dogzone pettable rect (128,270,144,188 in the 400×520 scene frame)."""
    g = geometry
    zx, zy, zw, zh = 128, 270, 144, 188  # #dogzone, index.html
    zx2, zy2 = zx + zw, zy + zh
    tail = g["tail"]
    belly = g["belly"]
    tx1, tx2 = max(tail["xAbove"], zx), zx2
    ty1, ty2 = max(tail["yAbove"], zy), zy2
    bx1, bx2 = max(belly["xAbove"], zx), min(belly["xBelow"], zx2)
    by1, by2 = max(belly["yAbove"], zy), zy2
    overlay = f"""
  <g class="hitboxes">
    <rect x="{zx}" y="{zy}" width="{zw}" height="{zh}" fill="none"
          stroke="#5a4636" stroke-width="1.4" stroke-dasharray="5 4" opacity=".8"/>
    <text x="{zx}" y="{zy - 5}" font-size="10" fill="#5a4636">pettable zone</text>
    <line x1="{zx}" y1="{g['earBelow']}" x2="{zx2}" y2="{g['earBelow']}"
          stroke="#8a5a2e" stroke-width="1.4" stroke-dasharray="3 3"/>
    <text x="{zx + 3}" y="{g['earBelow'] - 4}" font-size="10" fill="#8a5a2e">ear y&lt;{g['earBelow']}</text>
    <line x1="{zx}" y1="{g['headBelow']}" x2="{zx2}" y2="{g['headBelow']}"
          stroke="#8a5a2e" stroke-width="1.4" stroke-dasharray="3 3"/>
    <text x="{zx + 3}" y="{g['headBelow'] + 12}" font-size="10" fill="#8a5a2e">head y&lt;{g['headBelow']}</text>
    <rect x="{tx1}" y="{ty1}" width="{tx2 - tx1}" height="{ty2 - ty1}"
          fill="rgba(90,70,120,.18)" stroke="#5a4678" stroke-width="1.2"/>
    <text x="{tx2 - 4}" y="{ty1 + 12}" font-size="10" fill="#5a4678" text-anchor="end">tail</text>
    <rect x="{bx1}" y="{by1}" width="{bx2 - bx1}" height="{by2 - by1}"
          fill="rgba(46,110,74,.16)" stroke="#2e6e4a" stroke-width="1.2"/>
    <text x="{(bx1 + bx2) / 2:.0f}" y="{by2 - 6}" font-size="10" fill="#2e6e4a" text-anchor="middle">belly</text>
  </g>"""
    cell = _figure_cell(species_id, figure, coat_vars, "",
                        f"{coat_id} — hitboxes", variant="hitbox")
    return cell.replace("</svg>", overlay + "\n</svg>")


def render_board(pack_dir: Path) -> str:
    """Validate the pack and build the board HTML."""
    validated = validate_pack(pack_dir)
    record = validated.record
    css = STYLE_CSS.read_text(encoding="utf-8")

    sections: list[str] = []
    for species_id in record.species:
        assets = validated.species[species_id]
        figure = assets["figure"]
        coats = assets["coats"]
        geometry = assets["geometry"]
        coat_meta = " · ".join(
            f"<code>{html.escape(c)}</code> {p['body']}/{p['belly']}/{p['point']}"
            for c, p in coats.items()
        )
        pose_rows: list[str] = []
        for coat_id, palette in coats.items():
            vars_ = f"--dog-body:{palette['body']}; --dog-belly:{palette['belly']}; --dog-point:{palette['point']}"
            cells = [
                _figure_cell(species_id, figure, vars_, pose_class,
                             f"{coat_id} · {pose_label}")
                for pose_label, pose_class in POSES
            ]
            cells.append(
                _figure_cell(species_id, figure, vars_, "",
                             f"{coat_id} · at 60px", variant="small")
            )
            pose_rows.append('<div class="row">' + "\n".join(cells) + "</div>")
        hitbox_row = '<div class="row">' + "\n".join(
            _hitbox_cell(species_id, figure,
                         f"--dog-body:{p['body']}; --dog-belly:{p['belly']}; --dog-point:{p['point']}",
                         geometry, coat_id)
            for coat_id, p in coats.items()
        ) + "</div>"
        sections.append(f"""
<h2>species <code>{html.escape(species_id)}</code> — coats × poses</h2>
<p class="meta">{coat_meta}</p>
{''.join(pose_rows)}
<h2>species <code>{html.escape(species_id)}</code> — hitbox geometry over the art</h2>
<p class="meta"><code>{html.escape(json.dumps(geometry))}</code></p>
{hitbox_row}""")

    quirks = ", ".join(record.quirks) or "none"
    overlays = ", ".join(record.overlays) or "none"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pack render — {html.escape(record.name)} {html.escape(record.version)}</title>
<style>
{css}
</style>
<style>
{BOARD_CSS}
</style>
</head>
<body>
<h1>{html.escape(record.name)} <span class="meta">v{html.escape(record.version)}</span></h1>
<p class="meta">author {html.escape(record.author)} · license {html.escape(record.license)}
 · fx vocab v{record.fx_vocab_version} · quirks: {html.escape(quirks)} · overlays: {html.escape(overlays)}</p>
<p class="meta">validated and rendered from <code>{html.escape(str(pack_dir))}</code>
with Woolpack's bundled <code>style.css</code> — pose columns are the
<code>#wool-scene</code> state classes.</p>
{''.join(sections)}
</body>
</html>
"""


def main(argv: list[str] | None = None, *, prog: str | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="render a content pack to a static review board",
    )
    parser.add_argument("pack", type=Path, help="pack directory (e.g. packs/purl)")
    parser.add_argument("-o", "--output", type=Path, default=None,
                        help="output HTML path (default /tmp/<pack>-board.html)")
    args = parser.parse_args(argv)
    pack_dir = args.pack.expanduser().resolve()
    out = args.output or Path("/tmp") / f"{pack_dir.name}-board.html"
    board = render_board(pack_dir)
    out.write_text(board, encoding="utf-8")
    print(out)
    return 0


__all__ = ["BOARD_CSS", "POSES", "STYLE_CSS", "main", "render_board"]
