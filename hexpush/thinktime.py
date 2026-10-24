"""Quanto deve pensare il computer, e dove: misure per la gestione del tempo di MCTS.

    pypy3 -m hexpush.thinktime positions --games 100 --out results/thinktime
    pypy3 -m hexpush.thinktime match --policy early --sims 1000 --games 200 --out results/tt

positions: da partite mctsd(B) contro mctsd(B) prende le posizioni in cui c'è una scelta
(più di una mossa, nessuna vittoria immediata) e su ciascuna fa due ricerche, a B e a
8B simulazioni. Registra se scelgono la stessa mossa e la perdita stimata: quanto vale
per la ricerca lunga la mossa scelta dalla corta, rispetto alla sua migliore (in punti di
valore, cioè di probabilità di vittoria). Scrive <out>_posizioni.csv e un riassunto per
fase (semimossa e distanza dal traguardo).

match: la politica di gestione del tempo `--policy` (mcts.POLICIES) con budget B contro
mctsd fisso a `--fixed` simulazioni (default B), a colori alternati; riporta le vittorie e
le simulazioni medie per mossa. Il confronto onesto è a calcolo uguale: se la politica
spende in media più o meno di B, si sceglie B in modo che la media sia uguale a --fixed.
Scrive <out>_partite.csv e <out>_mosse.csv.

Senza opzioni sulle regole si usano quelle del sito (ko, no_pushback, due mosse per turno).
"""
import argparse
import csv
import math
import os
import random
import statistics
import time
from concurrent.futures import ProcessPoolExecutor

from .board import other
from .game import PASS, apply, immediate_outcomes, legal_moves, new_game
from .mcts import POLICIES, MCTSDecisiveBot
from .rules import Ruleset
from .simulate import add_rules_args, rules_from_args

SITE = dict(ko=True, no_pushback=True, moves_per_turn=2)
POS_FIELDS = ("seed", "semimossa", "mosse_rimaste", "n_mosse", "dist_mia", "dist_avv",
              "quota1", "quota2", "dietro", "uguale", "perdita", "valore_lungo")


def distance(state, player):
    """File che mancano alla pedina di `player` più vicina al traguardo."""
    R = state.rules.R
    rows = [R - r if p == "A" else r + R for (q, r), p in state.board.items() if p == player]
    return min(rows) if rows else 2 * R


def ranked(children):
    """Figli della radice per visite decrescenti (a parità, l'ordine della ricerca)."""
    return sorted(children, key=lambda ch: ch[1], reverse=True)


def root_stats(children):
    """(quota della 1a, quota della 2a, la 1a per visite non è la 1a per valore)."""
    rk = ranked(children)
    total = sum(ch[1] for ch in rk)
    second = rk[1][1] if len(rk) > 1 else 0
    best_q = max(rk, key=lambda ch: ch[2] / ch[1] if ch[1] else -1.0)
    return rk[0][1] / total, second / total, best_q[0] != rk[0][0]


# --- posizioni ---------------------------------------------------------------------------

def _positions_game(task):
    """Una partita mctsd(B) contro mctsd(B) e le due ricerche su ogni posizione con una
    scelta (una ogni `every`)."""
    rules, base, factor, seed, every = task
    rng = random.Random(f"tt-{seed}")
    players = {c: MCTSDecisiveBot(random.Random(f"tt-{seed}-{c}"), base) for c in "AB"}
    state = new_game(rules)
    rows = []
    while state.result is None:
        moves = legal_moves(state)
        if len(moves) > 1 and 1 not in immediate_outcomes(state, moves) \
                and rng.randrange(every) == 0:
            me = state.to_move
            short = MCTSDecisiveBot(random.Random(rng.getrandbits(64)), base)
            long_ = MCTSDecisiveBot(random.Random(rng.getrandbits(64)), base * factor)
            _, ch_short = short.search(state)
            _, ch_long = long_.search(state)
            choice = ranked(ch_short)[0][0]
            q_long = {m: v / n for m, n, v in ch_long if n}
            best = ranked(ch_long)[0][0]
            share1, share2, behind = root_stats(ch_short)
            rows.append({
                "seed": seed, "semimossa": state.ply, "mosse_rimaste": state.moves_left,
                "n_mosse": len(moves), "dist_mia": distance(state, me),
                "dist_avv": distance(state, other(me)), "quota1": round(share1, 4),
                "quota2": round(share2, 4), "dietro": int(behind),
                "uguale": int(choice == best),
                "perdita": round(q_long[best] - q_long[choice], 4) if choice in q_long else "",
                "valore_lungo": round(q_long[best], 4)})
        state = apply(state, players[state.to_move].choose(state))
    return rows


def _bins(rows, key, edges):
    """Righe raggruppate per `key` in intervalli [a, b)."""
    out = []
    for a, b in zip(edges, edges[1:]):
        sel = [r for r in rows if a <= r[key] < b]
        if sel:
            out.append((f"{a}-{b - 1}" if b - a > 1 else str(a), sel))
    return out


def _line(label, sel):
    losses = [r["perdita"] for r in sel if r["perdita"] != ""]
    big = sum(1 for x in losses if x > 0.05)
    return (f"  {label:>8}  {len(sel):5d}  {100 * statistics.mean(r['uguale'] for r in sel):5.1f}%"
            f"  {statistics.mean(losses):.4f}  {100 * big / len(losses):5.1f}%"
            f"  {statistics.mean(r['quota1'] for r in sel):.2f}")


def positions_summary(rows, base, factor):
    head = (f"  {'':>8}  {'pos':>5}  {'uguale':>6}  {'perdita':>7}  {'>0.05':>6}  quota1\n"
            f"  (stessa mossa a {base} e {base * factor} simulazioni; perdita media in "
            f"probabilità di vittoria; quota di visite della 1a mossa a {base})")
    plies = max(r["semimossa"] for r in rows) + 1
    parts = [head, "per semimossa:"]
    parts += [_line(lab, sel) for lab, sel in _bins(rows, "semimossa", [0, 4, 8, 12, 20, 30, 45,
                                                                     plies + 1])]
    parts.append("per distanza minima dal traguardo (la propria o quella avversaria):")
    for r in rows:
        r["dist"] = min(r["dist_mia"], r["dist_avv"])
    parts += [_line(lab, sel) for lab, sel in _bins(rows, "dist", [1, 2, 3, 4, 5, 6, 9])]
    parts.append("per quota di visite della 1a mossa a B:")
    for r in rows:
        r["q1"] = int(r["quota1"] * 10)
    parts += [_line(f"{lab}0%", sel) for lab, sel in _bins(rows, "q1", list(range(11)))]
    parts.append("per 'dietro' (la 1a per visite non è la 1a per valore):")
    parts += [_line(lab, sel) for lab, sel in _bins(rows, "dietro", [0, 1, 2])]
    parts.append(_line("totale", rows))
    return "\n".join(parts)


# --- incontri ----------------------------------------------------------------------------

def _match_game(task):
    rules, policy, base, fixed, adaptive_color, seed = task
    bots = {adaptive_color: MCTSDecisiveBot(random.Random(f"m-{seed}-a"), base,
                                            policy=POLICIES[policy]),
            other(adaptive_color): MCTSDecisiveBot(random.Random(f"m-{seed}-f"), fixed)}
    state = new_game(rules)
    moves = []
    t = time.perf_counter()
    while state.result is None:
        me = state.to_move
        bot = bots[me]
        move = bot.choose(state)
        if me == adaptive_color and bot.last_stats is not None:
            moves.append({"seed": seed, "semimossa": state.ply, "simulazioni": bot.last_sims,
                          "dist": min(distance(state, me), distance(state, other(me)))})
        state = apply(state, move)
    if state.result == "draw":
        outcome = "patta"
    else:
        outcome = "vittoria" if state.result == adaptive_color else "sconfitta"
    game = {"politica": policy, "budget": base, "fisso": fixed, "colore": adaptive_color,
            "esito": outcome,
            "motivo": state.reason, "semimosse": state.ply, "seed": seed,
            "secondi": round(time.perf_counter() - t, 1)}
    return game, moves


def match_summary(games, moves, policy, base, fixed):
    n = len(games)
    wins = sum(g["esito"] == "vittoria" for g in games)
    draws = sum(g["esito"] == "patta" for g in games)
    score = (wins + 0.5 * draws) / n
    se = math.sqrt(score * (1 - score) / n)
    sims = [m["simulazioni"] for m in moves]
    lines = [f"{policy}({base}) contro mctsd({fixed}): {n} partite, punteggio "
             f"{100 * score:.1f}% (±{100 * se:.1f}), patte {draws}",
             f"simulazioni per mossa: media {statistics.mean(sims):.0f} "
             f"({statistics.mean(sims) / fixed:.2f} volte il fisso), mediana {statistics.median(sims):.0f}, "
             f"min {min(sims)}, max {max(sims)}",
             "per distanza dal traguardo: mosse, simulazioni medie"]
    for d in sorted({m["dist"] for m in moves}):
        sel = [m["simulazioni"] for m in moves if m["dist"] == d]
        lines.append(f"  {d}: {len(sel):5d}  {statistics.mean(sel):7.0f}")
    lines.append("per semimossa: mosse, simulazioni medie")
    for a, b in ((0, 4), (4, 12), (12, 30), (30, 60), (60, 201)):
        sel = [m["simulazioni"] for m in moves if a <= m["semimossa"] < b]
        if sel:
            lines.append(f"  {a}-{b - 1}: {len(sel):5d}  {statistics.mean(sel):7.0f}")
    return "\n".join(lines)


def write_csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("what", choices=("positions", "match"))
    add_rules_args(ap)
    ap.add_argument("--sims", type=int, default=1000, help="budget B (simulazioni)")
    ap.add_argument("--fixed", type=int, help="match: simulazioni dell'avversario (default B)")
    ap.add_argument("--factor", type=int, default=8, help="positions: ricerca lunga = factor*B")
    ap.add_argument("--every", type=int, default=1,
                    help="positions: una posizione ogni tanti (a caso)")
    ap.add_argument("--policy", default="early", help="match: " + ", ".join(POLICIES))
    ap.add_argument("--games", type=int, default=100,
                    help="positions: partite; match: partite (pari, metà per colore)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--out", default="results/thinktime")
    args = ap.parse_args()
    rules = rules_from_args(args)
    if rules == Ruleset():
        rules = Ruleset(**SITE)
    fixed = args.fixed or args.sims
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    t = time.perf_counter()
    with ProcessPoolExecutor(args.jobs) as ex:
        if args.what == "positions":
            tasks = [(rules, args.sims, args.factor, args.seed + i, args.every)
                     for i in range(args.games)]
            rows = [r for part in ex.map(_positions_game, tasks) for r in part]
            write_csv(f"{args.out}_posizioni.csv", rows)
            text = positions_summary(rows, args.sims, args.factor)
        else:
            tasks = [(rules, args.policy, args.sims, fixed, "AB"[i % 2], args.seed + i)
                     for i in range(args.games)]
            results = list(ex.map(_match_game, tasks))
            games = [g for g, _ in results]
            moves = [m for _, ms in results for m in ms]
            write_csv(f"{args.out}_partite.csv", games)
            write_csv(f"{args.out}_mosse.csv", moves)
            text = match_summary(games, moves, args.policy, args.sims, fixed)
    text = f"{rules}\n{text}\n({time.perf_counter() - t:.0f} s)"
    with open(f"{args.out}_riassunto.txt", "w") as f:
        f.write(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
