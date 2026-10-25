"""Builds docs/articles/variety.html from the template and the result files.

    pypy3 docs/articles/src/build_variety.py

Needs results/corpus_*.jsonl, results/aperture.txt, results/strategie_stili.csv and
results/strategie16000_stili.csv (see hexpush.corpus, hexpush.openings, hexpush.strategies).
"""
import csv
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from figs import board_svg, hbar_svg, line_svg, profile_svg  # noqa: E402
from build_bots import DEFS  # noqa: E402

ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from hexpush.corpus import read  # noqa: E402
from hexpush.strategies import canonical, ending, first_move_name, replay, wilson  # noqa: E402

RES = ROOT / "results"
LEVELS = [("mctsd16000", "MCTS 16,000"), ("mctsd2000", "MCTS 2,000"), ("mctsd250", "MCTS 250"),
          ("greedy", "Greedy"), ("random", "Random")]
MOVE_EN = {"pedina centrale": "Centre piece forward",
           "pedina interna verso il centro": "Inner piece, toward the centre",
           "pedina interna verso il bordo": "Inner piece, toward the edge",
           "pedina esterna verso il centro": "Outer piece, toward the centre",
           "pedina esterna verso il bordo": "Outer piece, toward the edge"}


def corpus_table(games):
    out = ['<div class="tablewrap"><table><thead><tr><th>Players</th><th>Games</th>'
           '<th>A wins</th><th>Draws</th><th>Half-moves</th>'
           '<th>Pushes</th></tr></thead><tbody>']
    for key, name in LEVELS:
        g = games[key]
        n = len(g)
        mv = [m for x in g for m in x["moves"] if m]
        out.append(f"<tr><td>{name}</td><td>{n}</td>"
                   f"<td>{sum(x['result'] == 'A' for x in g) / n:.1%}</td>"
                   f"<td>{sum(x['result'] == 'draw' for x in g) / n:.0%}</td>"
                   f"<td>{sum(x['plies'] for x in g) / n:.0f}</td>"
                   f"<td>{sum(m[3] for m in mv) / len(mv):.0%}</td></tr>")
    out.append("</tbody></table></div>")
    out.append('<p class="tablenote">“A wins” is the first player’s share of all games; '
               '“Half-moves” the average game length; “Pushes” the share of moves that push '
               'a piece.</p>')
    return "\n".join(out)


def first_moves(games):
    values = {}
    text = (RES / "aperture.txt").read_text()
    block = text.split("== Dopo")[0]
    for line in block.splitlines():
        m = re.match(r"\s+(pedina .+?)\s{2,}([\d.]+)% \(([\d.]+)%\)\s+([\d.]+) \(([\d.]+)\)", line)
        if m:
            values[m.group(1).strip()] = (float(m.group(2)), float(m.group(4)))
    g = games["mctsd16000"]
    by = defaultdict(list)
    for x in g:
        by[first_move_name(canonical(x["moves"])[0], 4)].append(x["result"] == "A")
    out = ['<div class="tablewrap"><table><thead><tr><th>First move</th>'
           '<th>Chosen</th><th>A wins (95%)</th>'
           '<th>Value</th></tr></thead><tbody>']
    for k, v in sorted(by.items(), key=lambda kv: -len(kv[1])):
        lo, hi = wilson(sum(v), len(v))
        out.append(f"<tr><td class=\"l\">{MOVE_EN[k]}</td><td>{len(v) / len(g):.0%}</td>"
                   f"<td>{sum(v) / len(v):.0%} ({lo:.0%}–{hi:.0%})</td>"
                   f"<td>{values[k][1]:.3f}</td></tr>")
    out.append("</tbody></table></div>")
    out.append('<p class="tablenote">“Chosen”: how often the 16,000-simulation bot played '
               'this first move. “A wins”: the first player’s results after it, with a 95% '
               'interval. “Value”: the expected score for the first player according to the deep '
               'search described below.</p>')
    return "\n".join(out)


def openings_chart(games):
    depths = [1, 2, 3, 4]
    series = []
    cls = {"mctsd16000": "a", "mctsd2000": "b", "mctsd250": "n", "greedy": "n", "random": "n"}
    shown = [("mctsd16000", "MCTS 16,000"), ("mctsd2000", "MCTS 2,000"),
             ("random", "Random"), ("greedy", "Greedy")]
    for key, name in shown:
        can = [canonical(x["moves"]) for x in games[key]]
        ys = []
        for d in depths:
            c = Counter(tuple(x[:d]) for x in can if len(x) >= d)
            n = sum(c.values())
            coll = sum(v * (v - 1) for v in c.values()) / (n * (n - 1))
            ys.append(math.log10(1 / coll))
        series.append((name, ys, cls[key]))
        print(name, [round(10 ** y) for y in ys])
    labels = {0: "1", 1: "10", 2: "100", 3: "1,000", 4: "10,000"}
    return line_svg(series, depths, ymin=0, ymax=4, yticks=[0, 1, 2, 3, 4], ylabels=labels,
                    xlabel="opening length (half-moves)", xfmt="{}",
                    title="Effective number of openings by length",
                    label_dy={"MCTS 2,000": -10, "Random": 8, "MCTS 16,000": 0})


FEATS = [("pedine", "pieces ever moved"), ("concentrazione", "moves by busiest piece"),
         ("casa", "pieces kept at home"), ("punta", "reach of lead piece"),
         ("spinte_avv", "pushes on opponents"), ("ritirate", "backward moves"),
         ("laterali", "sideways moves"), ("contatto", "contact with opponents"),
         ("tempo", "turn of first crossing")]


def profiles():
    rows = list(csv.DictReader(open(RES / "strategie16000_stili.csv")))
    out = []
    for f, _ in FEATS:
        v = [float(r[f]) for r in rows]
        m = sum(v) / len(v)
        sd = math.sqrt(sum((x - m) ** 2 for x in v) / len(v))
        out.append((f, m, sd))
    groups = []
    for gid, title, cls in ((1, "Spear and wall", "a"), (0, "Scrum", "n"), (2, "Under siege", "b")):
        g = [r for r in rows if r["gruppo"] == str(gid)]
        zs = [(sum(float(r[f]) for r in g) / len(g) - m) / sd for f, m, sd in out]
        groups.append((title, zs, cls))
    return profile_svg([lab for _, lab in FEATS], groups)


def matchups():
    rows = list(csv.DictReader(open(RES / "strategie16000_stili.csv")))
    name = {"1": "Spear and wall", "0": "Scrum", "2": "Under siege"}
    games = defaultdict(dict)
    for r in rows:
        games[r["partita"]][r["colore"]] = r
    res = defaultdict(lambda: [0, 0])  # (style x, style y) -> [wins of x, games]
    for g in games.values():
        a, b = g["A"], g["B"]
        for me, opp in ((a, b), (b, a)):
            k = (me["gruppo"], opp["gruppo"])
            res[k][0] += float(me["vince"])
            res[k][1] += 1
    order = ["1", "0", "2"]
    out = ['<div class="tablewrap"><table><thead><tr><th>Style ↓ against →</th>']
    out += [f"<th>{name[o]}</th>" for o in order] + ["</tr></thead><tbody>"]
    for x in order:
        cells = []
        for y in order:
            w, n = res.get((x, y), (0, 0))
            if x == y:
                cells.append(f"<td>{n // 2} games</td>" if n else "<td>–</td>")
            else:
                cells.append(f"<td>{w / n:.0%} of {n}</td>" if n else "<td>–</td>")
        out.append(f"<tr><td>{name[x]}</td>{''.join(cells)}</tr>")
    out.append("</tbody></table></div>")
    out.append('<p class="tablenote">Share of games won by the style in the row against the '
               'style in the column, in games between two 16,000-simulation bots. On the '
               'diagonal, the number of games where both players fell in the same style.</p>')
    return "\n".join(out)


def endings_table(games):
    out = ['<div class="tablewrap"><table><thead><tr><th>Players</th>'
           '<th>Double threat</th><th>Relay finish</th>'
           '<th>Built in last turn</th></tr></thead><tbody>']
    for key, name in LEVELS[:3]:
        es = [e for e in (ending(g, replay(g)) for g in games[key]) if e]
        n = len(es)
        out.append(f"<tr><td>{name}</td>"
                   f"<td>{sum((e['subite'] or 0) >= 2 for e in es) / n:.0%}</td>"
                   f"<td>{sum(e['come'] == 'staffetta' for e in es) / n:.0%}</td>"
                   f"<td>{sum(e['pronte'] == 0 for e in es) / n:.0%}</td></tr>")
    out.append("</tbody></table></div>")
    out.append('<p class="tablenote">“Double threat”: at the start of the loser’s last turn, '
               'the winner had at least two different one-move wins. “Built in the final turn”: '
               'the winner had no one-move win when its final turn began.</p>')
    return "\n".join(out)


def fork_board(games):
    g = games["mctsd16000"][32]
    st = replay(g)
    pos = st[19].board
    runner = (2, -3)
    return board_svg(pos, arrows=[(runner, (2, -4), "b"), (runner, (3, -4), "b")],
                     marks=[(2, -4), (3, -4)], label="A double threat")


def dispersion():
    rows = list(csv.DictReader(open(RES / "strategie_stili.csv")))
    names = [k for k in rows[0] if k not in ("corpus", "partita", "colore", "bot", "vince",
                                             "semimosse", "gruppo")]
    fit = [r for r in rows if r["corpus"].startswith("mctsd")]
    mean = {f: sum(float(r[f]) for r in fit) / len(fit) for f in names}
    sd = {f: math.sqrt(sum((float(r[f]) - mean[f]) ** 2 for r in fit) / len(fit)) for f in names}

    def disp(g):
        Z = [[(float(r[f]) - mean[f]) / sd[f] for f in names] for r in g]
        m = [sum(c) / len(Z) for c in zip(*Z)]
        return sum(sum((a - b) ** 2 for a, b in zip(z, m)) for z in Z) / len(Z) / len(names)
    bars, err = [], None
    for key, name in [LEVELS[2], LEVELS[1], LEVELS[0], LEVELS[3], LEVELS[4]]:
        g = [r for r in rows if r["corpus"] == key]
        cls = "n" if key in ("greedy", "random") else "a"
        bars.append((f"{name}: all", disp(g), cls))
        bars.append((f"{name}: winners", disp([r for r in g if r["vince"] == "1"]), "b"))
    print([(b[0], round(b[1], 2)) for b in bars])
    return hbar_svg(bars, vmax=1.0, fmt="{:.2f}", ticks=(0, 0.25, 0.5, 0.75, 1.0),
                    label_w=200, bar_h=16, gap=6, title="Spread of playing behaviour")


def main():
    games = {k: read(RES / f"corpus_{k}.jsonl") for k, _ in LEVELS}
    tmpl = (HERE / "variety.tmpl.html").read_text()
    subs = {
        "{{STYLE}}": (HERE / "style.css").read_text(), "{{DEFS}}": DEFS,
        "{{CORPUS_TABLE}}": corpus_table(games),
        "{{FIRST_MOVES}}": first_moves(games),
        "{{OPENINGS_CHART}}": openings_chart(games),
        "{{PROFILES_CHART}}": profiles(),
        "{{MATCHUPS}}": matchups(),
        "{{FORK_BOARD}}": fork_board(games),
        "{{ENDINGS_TABLE}}": endings_table(games),
        "{{DISPERSION_CHART}}": dispersion(),
    }
    for k, v in subs.items():
        tmpl = tmpl.replace(k, v)
    assert "{{" not in tmpl, tmpl[tmpl.index("{{"):][:40]
    out = ROOT / "docs/articles/variety.html"
    out.write_text(tmpl)
    print(out, len(tmpl))


if __name__ == "__main__":
    main()
