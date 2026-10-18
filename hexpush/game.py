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


def position_key(board, to_move):
    """Identità di una posizione: campo + giocatore di turno."""
    return (frozenset(board.items()), to_move)


class GameState:
    """Stato immutabile per convenzione: nessuno deve modificare board o history.

    history: chiavi di tutte le posizioni dall'inizio, l'ultima è quella corrente.
    result: None in corso, 'A' o 'B' vincitore, 'draw' patta.
    reason: 'goal', 'repetition' o 'max_plies'.
    last_move: la mossa che ha portato qui (None all'inizio o dopo un passo).
    """

    __slots__ = ("rules", "geo", "board", "to_move", "ply", "history", "result", "reason",
                 "last_move")

    def __init__(self, rules, board, to_move, ply, history, result, reason, last_move):
        self.rules = rules
        self.geo = geometry(rules.R, rules.goal_wide)
        self.board = board
        self.to_move = to_move
        self.ply = ply
        self.history = history
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
    if history.count(history[-1]) >= 3:
        return "draw", "repetition"
    if ply >= rules.max_plies:
        return "draw", "max_plies"
    return None, None


def from_board(rules, board, to_move="A", ply=0):
    """Stato costruito a mano (test, analisi). La storia parte da questa posizione."""
    board = dict(board)
    geo = geometry(rules.R, rules.goal_wide)
    for cell, owner in board.items():
        if cell not in geo.cells or owner not in ("A", "B"):
            raise ValueError(f"cella non valida: {cell} -> {owner}")
    history = (position_key(board, to_move),)
    result, reason = _outcome(rules, geo, board, other(to_move), history, ply)
    return GameState(rules, board, to_move, ply, history, result, reason, None)


def new_game(rules=None):
    rules = rules or Ruleset()
    return from_board(rules, initial_board(rules.R, rules.N), "A")


def _moved_board(state, move):
    nbr = state.geo.nbr
    src = (move.q, move.r)
    dst = nbr[src][move.d]
    board = dict(state.board)
    mover = board.pop(src)
    if move.push:
        board[nbr[dst][move.d]] = board[dst]
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
    moves = []
    for (q, r), owner in board.items():
        if owner != player:
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
                if behind is not None and behind not in board:
                    moves.append(Move(q, r, d, True))
    if state.rules.ko and len(state.history) >= 2:
        forbidden = state.history[-2]
        after = other(player)
        moves = [m for m in moves if position_key(_moved_board(state, m), after) != forbidden]
    return moves or [PASS]


def apply(state, move):
    """Nuovo stato dopo `move`. Non controlla la legalità: la mossa viene da legal_moves."""
    if state.result is not None:
        raise ValueError("la partita è finita")
    board = state.board if move is PASS else _moved_board(state, move)
    to_move = other(state.to_move)
    ply = state.ply + 1
    history = state.history + (position_key(board, to_move),)
    result, reason = _outcome(state.rules, state.geo, board, state.to_move, history, ply)
    return GameState(state.rules, board, to_move, ply, history, result, reason, move)


def winner(state):
    """'A', 'B', 'draw' oppure None se la partita è in corso."""
    return state.result


def move_str(move, player=""):
    if move is PASS:
        return f"{player} passa".strip()
    verb = " spinge" if move.push else ""
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
        if m.push:
            moved.add(nbr[dst][m.d])
    if state.result is None:
        status = f"muove {state.to_move}"
    else:
        status = f"FINE: {state.result} ({state.reason})"
    lines = [f"semimossa {state.ply}, {status}"]
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
