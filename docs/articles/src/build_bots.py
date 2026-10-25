"""Builds docs/articles/bots.html from the template, the style and the ladder results.

    python3 docs/articles/src/build_bots.py results/ladder_nopushback_partite.csv \
        docs/articles/src/bots_ladder.txt
"""
import csv
import math
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from figs import board_svg, line_svg  # noqa: E402

ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from hexpush.board import initial_board  # noqa: E402

DEFS = """<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
<marker id="head-a" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path class="head-a" d="M0,0 L10,5 L0,10 z"/></marker>
<marker id="head-b" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path class="head-b" d="M0,0 L10,5 L0,10 z"/></marker>
<marker id="head-n" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path class="head-n" d="M0,0 L10,5 L0,10 z"/></marker>
</defs></svg>"""


def mcts_diagram():
    W, H = 640, 230
    pw = W / 4
    titles = ["1 · Selection", "2 · Expansion", "3 · Simulation", "4 · Backpropagation"]
    # tree layout inside a panel (relative x in [0,1], y in px)
    nodes = {"r": (0.5, 40), "a": (0.2, 90), "b": (0.5, 90), "c": (0.8, 90),
             "b1": (0.38, 140), "b2": (0.62, 140)}
    edges = [("r", "a"), ("r", "b"), ("r", "c"), ("b", "b1"), ("b", "b2")]
    path = {"r", "b", "b2"}
    out = [f'<svg class="diagram" viewBox="0 0 {W} {H}" role="img" '
           f'aria-label="The four steps of one MCTS simulation">']
    for k in range(4):
        x0 = k * pw
        X = lambda n: x0 + 12 + nodes[n][0] * (pw - 24)
        Y = lambda n: nodes[n][1]
        out.append(f'<text x="{x0 + pw / 2:.0f}" y="16" text-anchor="middle" '
                   f'font-weight="600">{titles[k]}</text>')
        for a, b in edges:
            hot = k in (0, 3) and a in path and b in path
            out.append(f'<line class="edge{" hot" if hot else ""}" x1="{X(a):.1f}" '
                       f'y1="{Y(a)}" x2="{X(b):.1f}" y2="{Y(b)}"/>')
        if k >= 1:
            nx, ny = X("b2"), 185
            out.append(f'<line class="edge{" hot" if k == 3 else ""}" x1="{X("b2"):.1f}" '
                       f'y1="{Y("b2")}" x2="{nx:.1f}" y2="{ny}"/>')
        for n in nodes:
            cls = "node hot" if (k in (0, 3) and n in path) else "node"
            out.append(f'<circle class="{cls}" cx="{X(n):.1f}" cy="{Y(n)}" r="10"/>')
        if k >= 1:
            cls = "node new" if k < 3 else "node hot"
            out.append(f'<circle class="{cls}" cx="{nx:.1f}" cy="{ny}" r="10"/>')
        if k == 0:
            out.append(f'<text class="cap" x="{x0 + pw / 2:.0f}" y="222" text-anchor="middle">'
                       f'follow the best UCB score</text>')
        if k == 1:
            out.append(f'<text class="cap" x="{x0 + pw / 2:.0f}" y="222" text-anchor="middle">'
                       f'add one untried move</text>')
        if k == 2:
            # random walk to the end of the game, drawn as a zigzag to the side
            sx, sy = nx + 12, 185
            pts = [(sx, sy)]
            for i in range(1, 7):
                pts.append((sx + i * 6, sy + (-1) ** i * 7 - i * 3))
            d = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(pts))
            out.append(f'<path class="walk" d="{d}"/>')
            ex, ey = pts[-1]
            out.append(f'<text x="{ex + 4:.1f}" y="{ey - 4:.1f}" font-weight="600">win</text>')
            out.append(f'<text class="cap" x="{x0 + pw / 2:.0f}" y="222" text-anchor="middle">'
                       f'random game to the end</text>')
        if k == 3:
            for n in ("r", "b", "b2"):
                out.append(f'<text class="cap" x="{X(n) + 14:.1f}" y="{Y(n) + 4}">+1</text>')
            out.append(f'<text class="cap" x="{nx + 14:.1f}" y="{ny + 4}">+1</text>')
            out.append(f'<text class="cap" x="{x0 + pw / 2:.0f}" y="222" text-anchor="middle">'
                       f'credit the result upward</text>')
        if k:
            out.append(f'<line x1="{x0:.0f}" y1="30" x2="{x0:.0f}" y2="205" '
                       f'class="edge" stroke-dasharray="2 4"/>')
    out.append("</svg>")
    return "\n".join(out)


def elo(p):
    return 400 * math.log10(p / (1 - p))


def ladder(path):
    rows = list(csv.DictReader(open(path)))
    by = {}
    for r in rows:
        by.setdefault((r["livello_forte"], r["livello_debole"]), []).append(r)
    order = sorted(by, key=lambda k: (not k[1].startswith("mctsd"),
                                      int(k[0].split(":")[1]), k[1]))
    table = ['<div class="tablewrap"><table><thead><tr><th>Stronger</th><th>Weaker</th>'
             '<th>Won–lost–drawn</th><th>Score</th><th>Elo</th>'
             '<th>95% interval</th></tr></thead><tbody>']
    chart = []
    for key in order:
        g = by[key]
        n = len(g)
        w = sum(r["esito"] == "vittoria" for r in g)
        l = sum(r["esito"] == "sconfitta" for r in g)
        d = n - w - l
        p = (w + d / 2) / n
        half = 1.96 * math.sqrt(p * (1 - p) / n)
        clip = lambda x: min(max(x, 0.5 / n), 1 - 0.5 / n)
        e, lo, hi = elo(clip(p)), elo(clip(p - half)), elo(clip(p + half))
        name = lambda s: (f"MCTS {int(s.split(':')[1]):,}" if s.startswith("mctsd")
                          else s.capitalize()).replace(",", " ")
        if not key[1].startswith("mctsd") and not any("sep" in t for t in table):
            table.append('<tr class="sep"><td colspan="6">Checks against the other bots</td></tr>')
        table.append(f"<tr><td>{name(key[0])}</td><td>{name(key[1])}</td>"
                     f"<td>{w}–{l}–{d}</td><td>{p:.1%}</td><td>{e:+.0f}</td>"
                     f"<td>{lo:+.0f} to {hi:+.0f}</td></tr>")
        if key[1].startswith("mctsd"):
            chart.append((int(key[0].split(":")[1]), e, lo, hi, p))
    table.append("</tbody></table></div>")
    return "\n".join(table), chart


def main():
    ladder_csv = sys.argv[1]
    tmpl = (HERE / "bots.tmpl.html").read_text()
    style = (HERE / "style.css").read_text()
    start = initial_board(4, 5)
    before = {(0, 0): "A", (1, 0): "B", (-1, -1): "A", (2, 1): "B", (-2, 2): "B"}
    after = {(1, 0): "A", (2, 0): "B", (-1, -1): "A", (2, 1): "B", (-2, 2): "B"}
    table, chart = ladder(ladder_csv)
    xs = [c[0] for c in chart]
    fig = line_svg([("Elo gained by doubling", [c[1] for c in chart], "a")], xs,
                   ymin=0, ymax=300, yticks=[0, 100, 200, 300], logx=True,
                   xlabel="simulations per move of the stronger side",
                   ylabel="", yfmt="{:+.0f}", xfmt="{:,}",
                   title="Elo gained at each doubling of the simulations",
                   errs=[(c[2], c[3]) for c in chart])
    subs = {
        "{{STYLE}}": style, "{{DEFS}}": DEFS,
        "{{BOARD_START}}": board_svg(start, label="Starting position"),
        "{{BOARD_PUSH1}}": board_svg(before, arrows=[((0, 0), (1, 0), "a")],
                                     label="Before the push"),
        "{{BOARD_PUSH2}}": board_svg(after, marks=[(2, 0)], label="After the push"),
        "{{MCTS_DIAGRAM}}": mcts_diagram(),
        "{{LADDER_TABLE}}": table,
        "{{LADDER_CHART}}": fig,
    }
    extra = Path(sys.argv[2]).read_text() if len(sys.argv) > 2 else ""
    for part in extra.split("@@")[1:]:
        k, _, v = part.partition("\n")
        subs["{{" + k.strip() + "}}"] = v.strip()
    for k, v in subs.items():
        tmpl = tmpl.replace(k, v)
    assert "{{" not in tmpl, tmpl[tmpl.index("{{"):][:40]
    out = ROOT / "docs/articles/bots.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(tmpl)
    print(out, len(tmpl), [(c[0], round(c[1])) for c in chart])


if __name__ == "__main__":
    main()
