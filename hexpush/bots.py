"""Bot. Interfaccia comune: bot = Classe(rng); bot.choose(state) -> mossa legale.

Ogni bot riceve il proprio random.Random: tutto è deterministico dato il seed.
"""
import math
import random

from .board import DIRS, OPPOSITE, other
from .game import PASS, Move, apply, immediate_outcomes, legal_moves, pushes_off

INF = math.inf


class RandomBot:
    name = "random"

    def __init__(self, rng=None):
        self.rng = rng or random.Random()

    def choose(self, state):
        return self.rng.choice(legal_moves(state))


def score_moves(state, moves):
    """Punteggio 1-ply di ogni mossa: variazione di
    somma(avanzamento proprio) - somma(avanzamento avversario), +inf se vince subito,
    -inf se consegna la vittoria all'avversario.

    Avanzamento di A = r + R, di B = R - r. Per A una pedina che si sposta di dr
    (propria o avversaria spinta) cambia il punteggio di +dr: spingere indietro un
    avversario vale quanto avanzare. Per B il segno è opposto.
    """
    sign = 1 if state.to_move == "A" else -1
    opp, R = other(state.to_move), state.rules.R
    scores = []
    for m, outcome in zip(moves, immediate_outcomes(state, moves)):
        if outcome:
            scores.append(outcome * INF)
        elif m is PASS:
            scores.append(0)
        elif pushes_off(state.geo, m):
            # la pedina eliminata toglie tutto il suo avanzamento all'avversario
            dst = state.geo.nbr[(m.q, m.r)][m.d]
            scores.append(sign * DIRS[m.d][1] + advancement(dst, opp, R))
        else:
            scores.append(sign * DIRS[m.d][1] * (2 if m.push else 1))
    return scores


def pick_best(rng, moves, scores):
    best = max(scores)
    return rng.choice([m for m, s in zip(moves, scores) if s == best])


class GreedyBot:
    name = "greedy"

    def __init__(self, rng=None):
        self.rng = rng or random.Random()

    def choose(self, state):
        moves = legal_moves(state)
        return pick_best(self.rng, moves, score_moves(state, moves))


def advancement(cell, player, R):
    return cell[1] + R if player == "A" else R - cell[1]


def winning_replies(state):
    """Quante mosse vincenti immediate ha il giocatore di turno."""
    if state.result is not None:
        return 0
    return sum(s == INF for s in score_moves(state, legal_moves(state)))


# --- Livello 2: bot patologici -------------------------------------------------------

class TurtleBot:
    """Non porta mai pedine oltre la seconda fila; tappa e respinge chi si avvicina.

    Per ogni mossa guarda la posizione risultante e minimizza, in quest'ordine:
    vittorie immediate concesse all'avversario, proprie pedine fuori dalle due file
    di casa, avanzamento totale dell'avversario.
    """
    name = "turtle"

    def __init__(self, rng=None):
        self.rng = rng or random.Random()

    def choose(self, state):
        moves = legal_moves(state)
        if moves == [PASS]:
            return PASS
        me, opp, R = state.to_move, other(state.to_move), state.rules.R
        scores = []
        for m in moves:
            nxt = apply(state, m)
            if nxt.result == me:
                scores.append(INF)
                continue
            if nxt.result == opp:
                scores.append(-INF)
                continue
            outside = opp_adv = 0
            for cell, owner in nxt.board.items():
                if owner == me:
                    outside += advancement(cell, me, R) > 1
                else:
                    opp_adv += advancement(cell, opp, R)
            # a metà turno (2 mosse per turno) non si guardano le minacce avversarie
            threats = winning_replies(nxt) if nxt.to_move == opp else 0
            scores.append(-10000 * threats - 100 * outside - opp_adv)
        return pick_best(self.rng, moves, scores)


class RusherBot:
    """Avanza una sola pedina (la più avanzata non ancora arrivata) il più in fretta
    possibile, le altre ferme.

    Se la corridora non può avanzare va di lato, poi indietro; se è bloccata del
    tutto (o ha già mosso in questo turno) muove un'altra pedina a caso, preferendo
    quelle fuori dalla fila di casa. Non consegna mai una vittoria immediata.
    """
    name = "rusher"

    def __init__(self, rng=None):
        self.rng = rng or random.Random()

    def choose(self, state):
        moves = legal_moves(state)
        if moves == [PASS]:
            return PASS
        scores = score_moves(state, moves)
        if max(scores) == INF:
            return pick_best(self.rng, moves, scores)
        me, R = state.to_move, state.rules.R
        sign = 1 if me == "A" else -1
        goal = state.geo.goal[me]
        # con K > 1 le pedine già arrivate restano ferme: corre la più avanzata delle altre
        own = [c for c, o in state.board.items() if o == me and c not in goal]
        top = max(advancement(c, me, R) for c in own)
        runner = self.rng.choice([c for c in own if advancement(c, me, R) == top])
        safe = [(m, s) for m, s in zip(moves, scores) if s != -INF] or list(zip(moves, scores))
        runs = [m for m, _ in safe if (m.q, m.r) == runner]
        if runs:
            return pick_best(self.rng, runs, [sign * DIRS[m.d][1] for m in runs])
        # corridora ferma (bloccata, o già mossa in questo turno): si muove un'altra
        # pedina, possibilmente non una del muro di casa
        home = state.geo.home[me]
        others = [m for m, _ in safe if m is not PASS and (m.q, m.r) not in home]
        return self.rng.choice(others or [m for m, _ in safe])


class PingPongBot(GreedyBot):
    """Se una sua pedina è appena stata spinta, la rispinge indietro; altrimenti greedy."""
    name = "pingpong"

    def choose(self, state):
        last = state.last_move
        if last is not None and last.push:
            nbr = state.geo.nbr
            dst = nbr[(last.q, last.r)][last.d]
            pushed = nbr[dst][last.d]
            if state.board.get(pushed) == state.to_move:
                back = Move(pushed[0], pushed[1], OPPOSITE[last.d], True)
                if back in legal_moves(state):
                    return back
        return super().choose(state)


from .mcts import MCTSBot, MCTSDecisiveBot  # noqa: E402  (mcts non dipende da bots)

BOTS = {cls.name: cls for cls in (RandomBot, GreedyBot, TurtleBot, RusherBot, PingPongBot,
                                  MCTSBot, MCTSDecisiveBot)}


def make_bot(spec, rng):
    """'greedy' -> GreedyBot(rng); 'mcts:2000:40' -> MCTSBot(rng, 2000, 40)."""
    name, *args = spec.split(":")
    return BOTS[name](rng, *(int(a) for a in args))
