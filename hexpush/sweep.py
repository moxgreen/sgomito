"""Griglia di parametri: per ogni Ruleset gioca le partite e salva una riga di metriche.

    python3 -m hexpush.sweep --out results/sweep_v1.csv

Per ogni configurazione: --games partite botA vs botB (metriche di gioco) e, se
--vs-random > 0, altrettante partite botA contro random a colori alterni.
"""
import argparse
import csv
import itertools
import os
import time
from concurrent.futures import ProcessPoolExecutor

from .rules import Ruleset
from .simulate import bot_score, run, summarize

GRID = {
    "R": (3, 4, 5),
    "N": (3, 4, 5, 6, 7, 8),
    "K": (1, 2),
    "goal_wide": (False, True),
    "push_own": (True, False),
    "ko": (False, True),
}

# soglie del CLAUDE.md
OK = {
    "draws": lambda s: s["draw_repetition"] + s["draw_max_plies"] < 0.10,
    "length": lambda s: 30 <= s["plies_mean"] <= 80,
    "first": lambda s: s["A_of_decided"] < 0.58,
}


def evaluate(task):
    rules, bot, games, vs_random, seed = task
    records = run(rules, bot, bot, games, seed)
    row = {k: getattr(rules, k) for k in ("R", "N", "K", "goal_wide", "push_own", "ko")}
    row.update(summarize(records))
    if vs_random:
        half = vs_random // 2
        rr = run(rules, bot, "random", half, seed, first_index=games)
        rr += run(rules, "random", bot, half, seed, first_index=games + half)
        row[f"{bot}_vs_random"] = bot_score(rr, bot)
    row["ok"] = "".join(name[0].upper() if ok(row) else "." for name, ok in OK.items())
    return row


def configs(grid):
    keys = list(grid)
    for values in itertools.product(*(grid[k] for k in keys)):
        kw = dict(zip(keys, values))
        try:
            yield Ruleset(**kw)
        except ValueError:
            pass


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bot", default="greedy")
    ap.add_argument("--games", type=int, default=2000)
    ap.add_argument("--vs-random", type=int, default=400)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--out", default="results/sweep.csv")
    for k, values in GRID.items():
        ap.add_argument(f"--{k}", help=f"valori separati da virgola (default {values})")
    args = ap.parse_args()

    grid = dict(GRID)
    for k in GRID:
        if getattr(args, k):
            parse = int if k in ("R", "N", "K") else (lambda v: v.lower() in ("1", "true"))
            grid[k] = tuple(parse(v) for v in getattr(args, k).split(","))

    tasks = [(r, args.bot, args.games, args.vs_random, args.seed) for r in configs(grid)]
    print(f"{len(tasks)} configurazioni, {args.games} partite {args.bot} vs {args.bot} ciascuna")
    t = time.perf_counter()
    with ProcessPoolExecutor(args.jobs) as ex:
        rows = list(ex.map(evaluate, tasks))
    print(f"fatto in {time.perf_counter() - t:.0f} s")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"scritto {args.out}")


if __name__ == "__main__":
    main()
