"""Raccolta di partite complete tra due bot, mossa per mossa, per le analisi di stile.

    pypy3 -m hexpush.corpus --ko --moves-per-turn 2 --bot mctsd:16000 --games 320 \
        --out results/corpus_mctsd16000.jsonl

Una riga JSON per partita: regole, bot, seed, esito e la lista delle mosse come
[q, r, d, spinta] (null per il passo). Per i bot MCTS anche, per ogni mossa, la quota
di visite della mossa scelta e il suo valore stimato (quanto il bot era convinto).
Le partite già presenti nel file (stesso seed) non si rigiocano: si può interrompere
e riprendere.
"""
import argparse
import dataclasses
import json
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

from .bots import make_bot
from .game import PASS, apply, new_game
from .simulate import add_rules_args, rules_from_args


def play(task):
    rules, spec_a, spec_b, seed = task
    t = time.perf_counter()
    bots = {c: make_bot(spec, random.Random(f"{seed}-{c}"))
            for c, spec in (("A", spec_a), ("B", spec_b))}
    state = new_game(rules)
    moves, conf = [], []
    while state.result is None:
        bot = bots[state.to_move]
        move = bot.choose(state)
        stats = getattr(bot, "last_stats", None)
        # quota di visite e valore della mossa scelta; None se obbligata o vincente
        conf.append(None if stats is None else [round(stats[1], 3), round(stats[3], 3)])
        moves.append(None if move is PASS else [move.q, move.r, move.d, int(move.push)])
        state = apply(state, move)
    return {"rules": dataclasses.asdict(rules), "botA": spec_a, "botB": spec_b,
            "seed": seed, "result": state.result, "reason": state.reason,
            "plies": state.ply, "moves": moves, "conf": conf,
            "seconds": round(time.perf_counter() - t, 1)}


def read(path):
    """Partite di un file del corpus."""
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_rules_args(ap)
    ap.add_argument("--bot", default="mctsd:2000", help="bot di entrambi i colori")
    ap.add_argument("--botB", help="bot di B, se diverso")
    ap.add_argument("--games", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rules = rules_from_args(args)
    spec_b = args.botB or args.bot
    seeds = [f"{args.seed}-{args.bot}-{spec_b}-{i}" for i in range(args.games)]
    done = {g["seed"] for g in read(args.out)} if os.path.exists(args.out) else set()
    tasks = [(rules, args.bot, spec_b, s) for s in seeds if s not in done]
    print(f"{rules}\n{args.bot} contro {spec_b}: {len(tasks)} partite da giocare "
          f"({len(done)} già nel file), {args.jobs} processi", flush=True)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    t0 = time.perf_counter()
    with open(args.out, "a") as f, ProcessPoolExecutor(args.jobs) as ex:
        futures = [ex.submit(play, t) for t in tasks]
        for k, fut in enumerate(as_completed(futures), 1):
            game = fut.result()
            f.write(json.dumps(game, separators=(",", ":")) + "\n")
            f.flush()
            if k % 10 == 0 or k == len(tasks):
                print(f"[{time.perf_counter() - t0:6.0f} s] {k}/{len(tasks)}", flush=True)


if __name__ == "__main__":
    main()
