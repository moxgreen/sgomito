"""Stato di gioco, mosse legali, applicazione delle mosse, esito.

Il campo è un dict (q, r) -> 'A'|'B' con solo le celle occupate. Uno stato non viene
mai modificato: apply() ne crea uno nuovo (serve per MCTS e per le ripetizioni).
"""
from typing import NamedTuple

from .board import DIR_NAMES, geometry, initial_board, other, row
from .rules import Ruleset


class Move(NamedTuple):
    q: int
    r: int
    d: int       # indice in board.DIRS
    push: bool   # True se la casella di arrivo è occupata e la pedina viene spinta


PASS = None  # unica mossa possibile quando non ce ne sono altre


def position_key(board, to_move, locked=None, moves_left=1):
    """Identità di una posizione: campo + giocatore di turno, più (con moves_per_turn=2)
    la pedina che ha già mosso in questo turno e quante mosse restano."""
    return (frozenset(board.items()), to_move, locked, moves_left)


class GameState:
    """Stato immutabile per convenzione: nessuno deve modificare board o history.

    history: chiavi di tutte le posizioni dall'inizio, l'ultima è quella corrente.
    turn_keys: chiavi delle posizioni d'inizio turno (servono al ko).
    moves_left: mosse che restano a chi muove in questo turno (1 o 2).
    locked: con 2 mosse per turno, la cella della pedina che ha già mosso (non può
    muovere di nuovo), altrimenti None.
    result: None in corso, 'A' o 'B' vincitore, 'draw' patta.
    reason: 'goal', 'elimination', 'repetition' o 'max_plies'.
    last_move: la mossa che ha portato qui (None all'inizio o dopo un passo).
    """

    __slots__ = ("rules", "geo", "board", "to_move", "ply", "history", "turn_keys",
                 "moves_left", "locked", "result", "reason", "last_move")

    def __init__(self, rules, board, to_move, ply, history, turn_keys, moves_left, locked,
                 result, reason, last_move):
        self.rules = rules
        self.geo = geometry(rules.R, rules.goal_wide)
        self.board = board
        self.to_move = to_move
        self.ply = ply
        self.history = history
        self.turn_keys = turn_keys
        self.moves_left = moves_left
        self.locked = locked
        self.result = result
        self.reason = reason
        self.last_move = last_move

    @property
    def key(self):
        return self.history[-1]

    def __repr__(self):
        return f"<GameState ply={self.ply} to_move={self.to_move} result={self.result}>"


def _outcome(rules, geo, board, mover, history, ply):
    """Esito dopo una mossa di `mover`. La vittoria ha la precedenza sulla patta."""
    reached = {"A": 0, "B": 0}
    for cell, owner in board.items():
        if cell in geo.goal[owner]:
            reached[owner] += 1
    # Una spinta può portare sul traguardo anche una pedina avversaria: si controllano
    # entrambi, e se arrivano insieme vince chi ha mosso.
    for p in (mover, other(mover)):
        if reached[p] >= rules.K:
            return p, "goal"
    if rules.push_off:
        # chi resta con meno di K pedine non può più vincere: perde
        left = {"A": 0, "B": 0}
        for owner in board.values():
            left[owner] += 1
        for p in (mover, other(mover)):
            if left[other(p)] < rules.K:
                return p, "elimination"
    if history.count(history[-1]) >= 3:
        return "draw", "repetition"
    if ply >= rules.max_plies:
        return "draw", "max_plies"
    return None, None


def from_board(rules, board, to_move="A", ply=0, moves_left=None):
    """Stato costruito a mano (test, analisi), all'inizio di un turno di `to_move`.
    La storia parte da questa posizione."""
    board = dict(board)
    geo = geometry(rules.R, rules.goal_wide)
    for cell, owner in board.items():
        if cell not in geo.cells or owner not in ("A", "B"):
            raise ValueError(f"cella non valida: {cell} -> {owner}")
    moves_left = rules.moves_per_turn if moves_left is None else moves_left
    history = (position_key(board, to_move, None, moves_left),)
    result, reason = _outcome(rules, geo, board, other(to_move), history, ply)
    return GameState(rules, board, to_move, ply, history, history, moves_left, None,
                     result, reason, None)


def new_game(rules=None):
    """Posizione iniziale. Con 2 mosse per turno A apre con una mossa sola."""
    rules = rules or Ruleset()
    return from_board(rules, initial_board(rules.R, rules.N), "A", moves_left=1)


def _moved_board(state, move):
    nbr = state.geo.nbr
    src = (move.q, move.r)
    dst = nbr[src][move.d]
    board = dict(state.board)
    mover = board.pop(src)
    if move.push:
        behind = nbr[dst][move.d]
        if behind is not None:
            board[behind] = board[dst]
        # altrimenti la pedina spinta esce dal campo (push_off): basta sovrascriverla
    board[dst] = mover
    return board


def legal_moves(state):
    """Lista delle mosse legali; [PASS] se non ce ne sono; [] a partita finita."""
    if state.result is not None:
        return []
    board = state.board
    nbr = state.geo.nbr
    player = state.to_move
    push_own = state.rules.push_own
    push_off = state.rules.push_off
    locked = state.locked
    moves = []
    for (q, r), owner in board.items():
        if owner != player or (q, r) == locked:
            continue
        around = nbr[(q, r)]
        for d in range(6):
            target = around[d]
            if target is None:
                continue
            occupant = board.get(target)
            if occupant is None:
                moves.append(Move(q, r, d, False))
            elif occupant != player or push_own:
                behind = nbr[target][d]
                if behind is None:
                    # fuori dal campo: solo con push_off e solo pedine avversarie
                    if push_off and occupant != player:
                        moves.append(Move(q, r, d, True))
                elif behind not in board:
                    moves.append(Move(q, r, d, True))
    if state.rules.ko and state.moves_left == 1 and len(state.turn_keys) >= 2:
        # ko: chi chiude il turno non può ricreare la posizione d'inizio del turno
        # precedente (con una mossa per turno: la posizione di 2 semimosse fa)
        forbidden = state.turn_keys[-2]
        after = other(player)
        ml = state.rules.moves_per_turn
        moves = [m for m in moves
                 if position_key(_moved_board(state, m), after, None, ml) != forbidden]
    return moves or [PASS]


def apply(state, move):
    """Nuovo stato dopo `move`. Non controlla la legalità: la mossa viene da legal_moves."""
    if state.result is not None:
        raise ValueError("la partita è finita")
    board = state.board if move is PASS else _moved_board(state, move)
    ply = state.ply + 1
    turn_keys = state.turn_keys
    if move is not PASS and state.moves_left > 1:
        # stesso turno: la pedina appena mossa resta ferma per la seconda mossa
        to_move, moves_left = state.to_move, state.moves_left - 1
        locked = state.geo.nbr[(move.q, move.r)][move.d]
        key = position_key(board, to_move, locked, moves_left)
    else:
        # il passo chiude sempre il turno
        to_move, moves_left, locked = other(state.to_move), state.rules.moves_per_turn, None
        key = position_key(board, to_move, None, moves_left)
        turn_keys = turn_keys + (key,)
    history = state.history + (key,)
    result, reason = _outcome(state.rules, state.geo, board, state.to_move, history, ply)
    return GameState(state.rules, board, to_move, ply, history, turn_keys, moves_left, locked,
                     result, reason, move)


def goal_counts(state):
    counts = {"A": 0, "B": 0}
    goal = state.geo.goal
    for cell, owner in state.board.items():
        if cell in goal[owner]:
            counts[owner] += 1
    return counts


def immediate_outcomes(state, moves):
    """Per ogni mossa: +1 se fa vincere subito chi muove, -1 se fa vincere
    l'avversario (spingendo una sua pedina sul traguardo), 0 altrimenti.
    Calcolato senza apply(): conta solo le celle che cambiano.
    """
    player, opp = state.to_move, other(state.to_move)
    K = state.rules.K
    geo = state.geo
    nbr, goal_p, goal_o = geo.nbr, geo.goal[player], geo.goal[opp]
    counts = goal_counts(state)
    opp_left = sum(1 for o in state.board.values() if o == opp)
    out = []
    for m in moves:
        if m is PASS:
            out.append(0)
            continue
        src = (m.q, m.r)
        dst = nbr[src][m.d]
        mine = counts[player] + (dst in goal_p) - (src in goal_p)
        theirs = counts[opp]
        if m.push:
            dst2 = nbr[dst][m.d]
            if dst2 is None:
                # pedina avversaria eliminata
                theirs -= dst in goal_o
                if mine < K and opp_left - 1 < K:
                    mine = K
            elif state.board[dst] == player:
                # la pedina spinta è mia: dst resta mia (ci va chi muove), dst2 si aggiunge
                mine += (dst2 in goal_p) - (dst in goal_p)
            else:
                theirs += (dst2 in goal_o) - (dst in goal_o)
        out.append(1 if mine >= K else -1 if theirs >= K else 0)
    return out


def winner(state):
    """'A', 'B', 'draw' oppure None se la partita è in corso."""
    return state.result


def pushes_off(geo, move):
    """True se la mossa spinge una pedina fuori dal campo."""
    if move is PASS or not move.push:
        return False
    dst = geo.nbr[(move.q, move.r)][move.d]
    return geo.nbr[dst][move.d] is None


def move_str(move, player="", geo=None):
    if move is PASS:
        return f"{player} passa".strip()
    verb = " spinge" if move.push else ""
    if geo is not None and pushes_off(geo, move):
        verb = " butta fuori"
    return f"{player} ({move.q},{move.r}) {DIR_NAMES[move.d]}{verb}".strip()


def render(state, coords=True):
    """Campo in ASCII. Le pedine mosse nell'ultima semimossa sono in minuscolo."""
    R = state.rules.R
    moved = set()
    m = state.last_move
    if m is not None:
        nbr = state.geo.nbr
        dst = nbr[(m.q, m.r)][m.d]
        moved.add(dst)
        if m.push and nbr[dst][m.d] is not None:
            moved.add(nbr[dst][m.d])
    if state.result is None:
        status = f"muove {state.to_move}"
        if state.rules.moves_per_turn == 2:
            if state.locked is not None:
                status += " (seconda mossa)"
            else:
                status += " (due mosse)" if state.moves_left == 2 else " (una mossa)"
    else:
        status = f"FINE: {state.result} ({state.reason})"
    lines = [f"semimossa {state.ply}, {status}"]
    if state.rules.push_off:
        left = [sum(1 for o in state.board.values() if o == p) for p in "AB"]
        lines[0] += f"   pedine A {left[0]}, B {left[1]}"
    for r in range(-R, R + 1):
        cells = row(R, r)
        marks = []
        for c in cells:
            p = state.board.get(c, ".")
            marks.append(p.lower() if c in moved else p)
        body = " " * abs(r) + " ".join(marks)
        if coords:
            body = f"r={r:+d} " + body.ljust(4 * R + 1) + f"  q {cells[0][0]}..{cells[-1][0]}"
        lines.append(body)
    return "\n".join(lines)


def _demo():
    """Una partita a mosse casuali stampata passo passo: python -m hexpush.game"""
    import argparse
    import random

    ap = argparse.ArgumentParser(description=_demo.__doc__)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--R", type=int, default=4)
    ap.add_argument("--N", type=int, default=5)
    ap.add_argument("--plies", type=int, default=30, help="semimosse da stampare")
    args = ap.parse_args()
    rng = random.Random(args.seed)
    state = new_game(Ruleset(R=args.R, N=args.N))
    print(render(state), "\n")
    while state.result is None:
        move = rng.choice(legal_moves(state))
        mover = state.to_move
        state = apply(state, move)
        if state.ply <= args.plies or state.result is not None:
            print(move_str(move, mover))
            print(render(state), "\n")
    print(f"esito: {state.result} ({state.reason}) dopo {state.ply} semimosse")


if __name__ == "__main__":
    _demo()
