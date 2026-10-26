"""Self-play: gioca partite tra due bot e stampa le metriche.

    python3 -m hexpush.simulate --botA greedy --botB random --games 2000 --swap

Con --swap si gioca lo stesso numero di partite a colori invertiti, e si riporta
anche la percentuale di vittorie di ciascun bot indipendentemente dal colore.
"""
import argparse
import csv
import os
import random
import statistics
import time
from concurrent.futures import ProcessPoolExecutor

from .bots import make_bot
from .game import PASS, apply, new_game
from .rules import Ruleset

FIELDS = ("game", "botA", "botB", "result", "reason", "plies", "moves", "pushes", "passes")


def play_game(rules, spec_a, spec_b, seed, i):
    bots = {
        "A": make_bot(spec_a, random.Random(f"{seed}-{i}-A")),
        "B": make_bot(spec_b, random.Random(f"{seed}-{i}-B")),
    }
    state = new_game(rules)
    moves = pushes = passes = 0
    while state.result is None:
        move = bots[state.to_move].choose(state)
        if move is PASS:
            passes += 1
        else:
            moves += 1
            pushes += move.push
        state = apply(state, move)
    return {"game": i, "botA": spec_a, "botB": spec_b, "result": state.result,
            "reason": state.reason, "plies": state.ply, "moves": moves, "pushes": pushes,
            "passes": passes}


def _play_chunk(args):
    rules, spec_a, spec_b, seed, indices = args
    return [play_game(rules, spec_a, spec_b, seed, i) for i in indices]


def run(rules, spec_a, spec_b, games, seed=0, jobs=1, first_index=0):
    indices = list(range(first_index, first_index + games))
    if jobs <= 1:
        return _play_chunk((rules, spec_a, spec_b, seed, indices))
    chunks = [indices[k::jobs * 4] for k in range(jobs * 4)]
    with ProcessPoolExecutor(jobs) as ex:
        parts = ex.map(_play_chunk, [(rules, spec_a, spec_b, seed, c) for c in chunks if c])
    records = [r for part in parts for r in part]
    records.sort(key=lambda r: r["game"])
    return records


def _pct(x, n):
    return x / n if n else float("nan")


def summarize(records):
    """Le metriche del CLAUDE.md, per colore."""
    n = len(records)
    a = sum(r["result"] == "A" for r in records)
    b = sum(r["result"] == "B" for r in records)
    rep = sum(r["reason"] == "repetition" for r in records)
    maxp = sum(r["reason"] == "max_plies" for r in records)
    plies = sorted(r["plies"] for r in records)
    decided = sorted(r["plies"] for r in records if r["reason"] == "goal")
    q = statistics.quantiles(plies, n=10) if n >= 2 else [plies[0]] * 9
    moves = sum(r["moves"] for r in records)
    return {
        "games": n,
        "A_win": _pct(a, n), "B_win": _pct(b, n),
        "A_of_decided": _pct(a, a + b),
        "draw_repetition": _pct(rep, n), "draw_max_plies": _pct(maxp, n),
        "plies_mean": statistics.fmean(plies),
        "plies_p10": q[0], "plies_median": statistics.median(plies), "plies_p90": q[-1],
        "plies_mean_decided": statistics.fmean(decided) if decided else float("nan"),
        "push_rate": _pct(sum(r["pushes"] for r in records), moves),
        "pass_rate": _pct(sum(r["passes"] for r in records), moves),
    }


def bot_score(records, spec):
    """Punti del bot `spec` (vittoria 1, patta 0.5) su tutte le partite in cui gioca."""
    pts = n = 0
    for r in records:
        for color in ("A", "B"):
            if r[f"bot{color}"] == spec:
                n += 1
                pts += 1 if r["result"] == color else 0.5 if r["result"] == "draw" else 0
    return _pct(pts, n)


def format_summary(s):
    return (
        f"partite {s['games']}: A {s['A_win']:.1%}  B {s['B_win']:.1%}  "
        f"patte rip. {s['draw_repetition']:.1%}  patte limite {s['draw_max_plies']:.1%}\n"
        f"  A sulle decise {s['A_of_decided']:.1%}   spinte {s['push_rate']:.1%}"
        f"   passi {s['pass_rate']:.2%}\n"
        f"  semimosse: media {s['plies_mean']:.1f} (decise {s['plies_mean_decided']:.1f})"
        f"  p10 {s['plies_p10']:.0f}  mediana {s['plies_median']:.0f}  p90 {s['plies_p90']:.0f}"
    )


def rules_from_args(args):
    return Ruleset(R=args.R, N=args.N, K=args.K, goal_wide=args.goal_wide,
                   push_own=not args.no_push_own, ko=args.ko, no_pushback=not args.pushback,
                   push_off=args.push_off, moves_per_turn=args.moves_per_turn,
                   first_turn_moves=args.first_turn_moves,
                   max_plies=args.max_plies)


def add_rules_args(ap):
    d = Ruleset()
    ap.add_argument("--R", type=int, default=d.R)
    ap.add_argument("--N", type=int, default=d.N)
    ap.add_argument("--K", type=int, default=d.K)
    ap.add_argument("--goal-wide", action="store_true")
    ap.add_argument("--no-push-own", action="store_true")
    ap.add_argument("--ko", action="store_true")
    ap.add_argument("--pushback", action="store_true",
                    help="toglie no_pushback (di default è vietato rifare al contrario una "
                         "spinta subita nell'ultimo turno)")
    ap.add_argument("--push-off", action="store_true",
                    help="spingere fuori dal campo una pedina avversaria la elimina")
    ap.add_argument("--moves-per-turn", type=int, default=d.moves_per_turn,
                    help="2: due mosse per turno con pedine diverse (A apre con una sola)")
    ap.add_argument("--first-turn-moves", type=int, default=d.first_turn_moves,
                    help="mosse del primo turno di A (con --moves-per-turn 2)")
    ap.add_argument("--max-plies", type=int, default=d.max_plies)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_rules_args(ap)
    ap.add_argument("--games", type=int, default=2000)
    ap.add_argument("--botA", default="greedy")
    ap.add_argument("--botB", default="greedy")
    ap.add_argument("--swap", action="store_true", help="gioca anche a colori invertiti")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--csv", help="file CSV con una riga per partita")
    args = ap.parse_args()

    rules = rules_from_args(args)
    print(rules)
    t = time.perf_counter()
    records = run(rules, args.botA, args.botB, args.games, args.seed, args.jobs)
    if args.swap:
        records += run(rules, args.botB, args.botA, args.games, args.seed, args.jobs,
                       first_index=args.games)
    dt = time.perf_counter() - t

    print(f"\n{args.botA} (A) vs {args.botB} (B)")
    print(format_summary(summarize(records[:args.games])))
    if args.swap:
        print(f"\n{args.botB} (A) vs {args.botA} (B)")
        print(format_summary(summarize(records[args.games:])))
        if args.botA != args.botB:
            print(f"\npunti {args.botA}: {bot_score(records, args.botA):.1%}   "
                  f"punti {args.botB}: {bot_score(records, args.botB):.1%}")
    print(f"\n{len(records)} partite in {dt:.1f} s ({len(records) / dt:.0f} partite/s, "
          f"{args.jobs} processi)")

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(records)


if __name__ == "__main__":
    main()
