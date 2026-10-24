"""Ricerca profonda sulle prime mosse: quanto conta l'apertura?

    pypy3 -m hexpush.openings --ko --moves-per-turn 2 --sims 400000 --searches 8 \
        --out results/aperture.txt

Per la posizione iniziale e per ciascuna prima mossa di A (a meno della simmetria
destra-sinistra) si fanno `searches` ricerche MCTSDecisiveBot indipendenti con seed
diversi. Per ogni mossa possibile si riportano la quota di visite media e il valore
stimato per chi muove, con la deviazione standard tra le ricerche: se due mosse
differiscono meno di questa, la ricerca non le distingue.
"""
import argparse
import math
import os
import random
import statistics
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

from .board import DIR_NAMES
from .game import apply, legal_moves, new_game
from .mcts import MCTSDecisiveBot
from .simulate import add_rules_args, rules_from_args
from .strategies import MIRROR_DIR, first_move_name


def mirror(move):
    return type(move)(-move.q - move.r, move.r, MIRROR_DIR[move.d], move.push)


def search(task):
    state, sims, seed = task
    _, children = MCTSDecisiveBot(random.Random(seed), sims).search(state)
    total = sum(n for _, n, _ in children)
    return {m: (n / total, v / n) for m, n, v in children}


def analyse(ex, state, sims, searches, label, fold_mirror):
    seeds = [f"{label}-{i}" for i in range(searches)]
    results = list(ex.map(search, [(state, sims, s) for s in seeds]))
    share, value = defaultdict(list), defaultdict(list)
    for res in results:
        merged = defaultdict(lambda: [0.0, 0.0])
        for m, (s, v) in res.items():
            key = min(m, mirror(m)) if fold_mirror else m
            merged[key][0] += s
            merged[key][1] += s * v
        for key, (s, sv) in merged.items():
            share[key].append(s)
            value[key].append(sv / s if s else float("nan"))
    rows = []
    for key in share:
        s, v = share[key], value[key]
        rows.append((key, statistics.fmean(s), statistics.pstdev(s), statistics.fmean(v),
                     statistics.pstdev(v)))
    rows.sort(key=lambda r: -r[1])
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_rules_args(ap)
    ap.add_argument("--sims", type=int, default=400000)
    ap.add_argument("--searches", type=int, default=8)
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--out", default="results/aperture.txt")
    args = ap.parse_args()
    rules = rules_from_args(args)
    out = [str(rules), f"{args.searches} ricerche da {args.sims} simulazioni per posizione", ""]
    say = lambda *ls: (out.extend(ls), print(*ls, sep="\n", flush=True))
    t0 = time.perf_counter()
    start = new_game(rules)
    with ProcessPoolExecutor(args.jobs) as ex:
        say("== Prima mossa di A (mosse simmetriche sommate)",
            "  mossa                          visite (sd)       valore per A (sd)")
        rows = analyse(ex, start, args.sims, args.searches, "start", True)
        for m, s, ss, v, vs in rows:
            say(f"  {first_move_name((m.q, m.r, m.d), rules.R):30s} {s:6.1%} ({ss:4.1%})"
                f"   {v:.3f} ({vs:.3f})")
        firsts = [m for m, *_ in rows]
        say("")
        for m in firsts:
            state = apply(start, m)
            say(f"== Dopo {first_move_name((m.q, m.r, m.d), rules.R)} ({m.q},{m.r}"
                f" {DIR_NAMES[m.d]}): prima mossa di B",
                "  mossa              visite (sd)       valore per B (sd)")
            for b, s, ss, v, vs in analyse(ex, state, args.sims, args.searches,
                                           f"{m}", False)[:8]:
                push = " spinge" if b.push else ""
                say(f"  ({b.q:+d},{b.r:+d}) {DIR_NAMES[b.d]:2s}{push:7s}  {s:6.1%} ({ss:4.1%})"
                    f"   {v:.3f} ({vs:.3f})")
            say(f"  [{time.perf_counter() - t0:.0f} s]", "")
    with open(args.out, "w") as f:
        f.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
