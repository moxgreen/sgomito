"""Test della curva a raddoppi per MCTS: protocollo in docs/curva_raddoppi.md.

    pypy3 -m hexpush.ladder --ko --games 200 --out results/ladder_v1

Ogni livello di simulazioni gioca contro quello col doppio, più alcuni incontri di
controllo. Con --against BOT ogni livello gioca invece contro BOT. Colori alternati, seed salvati. Scrive:
  <out>_partite.csv  una riga per partita
  <out>_mosse.csv    una riga per ogni mossa MCTS: figli e quote di visita
  <out>_riassunto.txt la tabella stampata a video
"""
import argparse
import csv
import math
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

from .board import other
from .bots import make_bot
from .game import PASS, apply, new_game
from .simulate import add_rules_args, rules_from_args

LEVELS = (100, 200, 400, 800, 1600, 3200)
CHECKS = ((100, "random"), (2000, "greedy"), (2000, "turtle"), (2000, "rusher"),
          (2000, "pingpong"))

GAME_FIELDS = ("livello_forte", "livello_debole", "colore_forte", "esito", "motivo",
               "semimosse", "n_spinte", "seed", "secondi")
MOVE_FIELDS = ("seed", "semimossa", "bot", "n_figli", "quota1", "quota2", "valore1")


def sims(spec):
    name, *args = spec.split(":")
    return int(args[0]) if name.startswith("mcts") and args else 0


def play(task):
    rules, strong, weak, strong_color, seed = task
    t = time.perf_counter()
    specs = {strong_color: strong, other(strong_color): weak}
    bots = {c: make_bot(specs[c], random.Random(f"{seed}-{c}")) for c in "AB"}
    state = new_game(rules)
    pushes = 0
    trace = []
    while state.result is None:
        bot = bots[state.to_move]
        move = bot.choose(state)
        stats = getattr(bot, "last_stats", None)
        if stats is not None:
            trace.append({"seed": seed, "semimossa": state.ply, "bot": specs[state.to_move],
                          "n_figli": stats[0], "quota1": round(stats[1], 4),
                          "quota2": round(stats[2], 4), "valore1": round(stats[3], 4)})
        if move is not PASS:
            pushes += move.push
        state = apply(state, move)
    if state.result == "draw":
        outcome = "patta"
    else:
        outcome = "vittoria" if state.result == strong_color else "sconfitta"
    game = {"livello_forte": strong, "livello_debole": weak, "colore_forte": strong_color,
            "esito": outcome, "motivo": state.reason, "semimosse": state.ply,
            "n_spinte": pushes, "seed": seed, "secondi": round(time.perf_counter() - t, 2)}
    return game, trace


def elo(p):
    return 400 * math.log10(p / (1 - p))


def summarize(games, moves):
    n = len(games)
    w = sum(g["esito"] == "vittoria" for g in games)
    l = sum(g["esito"] == "sconfitta" for g in games)
    d = n - w - l
    p = (w + 0.5 * d) / n
    half = 1.96 * math.sqrt(p * (1 - p) / n)
    clip = lambda x: min(max(x, 0.5 / n), 1 - 0.5 / n)  # evita ±infinito a 0% e 100%
    row = {"n": n, "V": w, "S": l, "P": d, "punti": p, "elo": elo(clip(p)),
           "elo_lo": elo(clip(p - half)), "elo_hi": elo(clip(p + half)),
           "semimosse": sum(g["semimosse"] for g in games) / n,
           "secondi": sum(g["secondi"] for g in games) / n}
    strong = games[0]["livello_forte"]
    own = [m for m in moves if m["bot"] == strong]
    if own:
        row["quota1"] = sum(m["quota1"] for m in own) / len(own)
        row["indecise"] = sum(m["quota1"] - m["quota2"] < 0.05 for m in own) / len(own)
    return row


def format_table(rules, matches, by_match, moves_by_match, n_checks):
    lines = [str(rules), "",
             f"{'forte':>10} {'debole':>10} {'n':>4} {'V':>4} {'S':>4} {'P':>4} {'punti':>7}"
             f" {'Elo':>6} {'IC 95%':>13} {'semim.':>6} {'s/partita':>9} {'quota1':>6}"
             f" {'indecise':>8}"]
    for i, (strong, weak) in enumerate(matches):
        if n_checks and i == len(matches) - n_checks:
            lines.append("-- controlli")
        games = by_match.get((strong, weak))
        if not games:
            continue
        s = summarize(games, moves_by_match.get((strong, weak), []))
        lines.append(
            f"{strong:>10} {weak:>10} {s['n']:4d} {s['V']:4d} {s['S']:4d} {s['P']:4d}"
            f" {s['punti']:7.1%} {s['elo']:+6.0f} {s['elo_lo']:+6.0f}..{s['elo_hi']:+5.0f}"
            f" {s['semimosse']:6.1f} {s['secondi']:9.1f} {s.get('quota1', float('nan')):6.1%}"
            f" {s.get('indecise', float('nan')):8.1%}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_rules_args(ap)
    ap.add_argument("--games", type=int, default=200, help="partite per incontro (pari)")
    ap.add_argument("--levels", default=",".join(map(str, LEVELS)))
    ap.add_argument("--bot", default="mcts", help="mcts (puro) o mctsd (mosse decisive, c=0.5)")
    ap.add_argument("--no-checks", action="store_true")
    ap.add_argument("--against", help="invece dei raddoppi: ogni livello contro questo bot")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--out", default="results/ladder")
    args = ap.parse_args()

    rules = rules_from_args(args)
    levels = [int(x) for x in args.levels.split(",")]
    if args.against:
        matches = [(f"{args.bot}:{n}", args.against) for n in levels]
    else:
        matches = [(f"{args.bot}:{hi}", f"{args.bot}:{lo}") for lo, hi in zip(levels, levels[1:])]
    n_checks = 0 if args.against or args.no_checks else len(CHECKS)
    if n_checks:
        matches += [(f"{args.bot}:{n}", opp) for n, opp in CHECKS]
    tasks = [(rules, strong, weak, "AB"[i % 2], f"{args.seed}-{strong}-{weak}-{i}")
             for strong, weak in matches for i in range(args.games)]
    # prima le partite più lente, per bilanciare i processi
    tasks.sort(key=lambda t: -(sims(t[1]) + sims(t[2])))
    print(f"{rules}\n{len(matches)} incontri, {len(tasks)} partite, {args.jobs} processi",
          flush=True)

    by_match, moves_by_match = {}, {}
    games_out, moves_out = [], []
    t0 = time.perf_counter()
    with ProcessPoolExecutor(args.jobs) as ex:
        futures = [ex.submit(play, t) for t in tasks]
        for k, fut in enumerate(as_completed(futures), 1):
            game, trace = fut.result()
            key = (game["livello_forte"], game["livello_debole"])
            by_match.setdefault(key, []).append(game)
            moves_by_match.setdefault(key, []).extend(trace)
            games_out.append(game)
            moves_out.extend(trace)
            if len(by_match[key]) == args.games:
                print(f"[{time.perf_counter() - t0:6.0f} s] finito {key[0]} vs {key[1]}"
                      f" ({k}/{len(tasks)} partite)", flush=True)

    table = format_table(rules, matches, by_match, moves_by_match, n_checks)
    table += f"\n\ntempo totale {time.perf_counter() - t0:.0f} s"
    print("\n" + table)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    games_out.sort(key=lambda g: g["seed"])
    for path, fields, rows in ((f"{args.out}_partite.csv", GAME_FIELDS, games_out),
                               (f"{args.out}_mosse.csv", MOVE_FIELDS, moves_out)):
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    with open(f"{args.out}_riassunto.txt", "w") as f:
        f.write(table + "\n")


if __name__ == "__main__":
    main()
