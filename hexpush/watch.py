"""Guarda una partita tra due bot, mossa per mossa.

    python3 -m hexpush.watch --botA mcts:500 --botB rusher --delay 0.5

Le pedine mosse nell'ultima semimossa sono in minuscolo (vedi game.render).
"""
import argparse
import random
import time

from .bots import make_bot
from .game import apply, move_str, new_game, render
from .simulate import add_rules_args, rules_from_args


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    add_rules_args(ap)
    ap.add_argument("--botA", default="mcts:500")
    ap.add_argument("--botB", default="rusher")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--delay", type=float, default=0.0, help="secondi di pausa tra le mosse")
    args = ap.parse_args()

    bots = {
        "A": make_bot(args.botA, random.Random(f"{args.seed}-A")),
        "B": make_bot(args.botB, random.Random(f"{args.seed}-B")),
    }
    state = new_game(rules_from_args(args))
    print(f"A = {args.botA}   B = {args.botB}   seed {args.seed}\n")
    print(render(state), "\n")
    while state.result is None:
        mover = state.to_move
        t0 = time.perf_counter()
        move = bots[mover].choose(state)
        dt = time.perf_counter() - t0
        state = apply(state, move)
        print(f"{move_str(move, mover, state.geo)}   [{args.botA if mover == 'A' else args.botB}, {dt:.2f}s]")
        print(render(state), "\n", flush=True)
        if args.delay:
            time.sleep(args.delay)
    print(f"esito: {state.result} ({state.reason}) dopo {state.ply} semimosse")


if __name__ == "__main__":
    main()
