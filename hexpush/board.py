"""Geometria dell'esagono in coordinate assiali (q, r).

A parte dal bordo r = -R e punta a r = R; B il contrario.
Nel disegno ASCII r cresce verso il basso, quindi A sta in alto.
"""
from functools import lru_cache

# Ordine fissato dal CLAUDE.md; i nomi valgono per il disegno ASCII (r verso il basso).
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, -1), (-1, 1))
DIR_NAMES = ("E", "W", "SE", "NW", "NE", "SW")
OPPOSITE = (1, 0, 3, 2, 5, 4)

PLAYERS = ("A", "B")


def other(player):
    return "B" if player == "A" else "A"


def on_board(R, q, r):
    return abs(q) <= R and abs(r) <= R and abs(q + r) <= R


def row(R, r):
    """Celle della fila r, da sinistra a destra (q crescente)."""
    return [(q, r) for q in range(max(-R, -R - r), min(R, R - r) + 1)]


class Geometry:
    """Tutto ciò che dipende solo da R e goal_wide, calcolato una volta sola.

    nbr[cella][d] è la cella adiacente in direzione DIRS[d], o None se fuori campo.
    home[p] è la fila di partenza di p, goal[p] le celle dove p deve arrivare.
    """

    __slots__ = ("R", "cells", "nbr", "home", "goal")

    def __init__(self, R, goal_wide):
        self.R = R
        self.cells = frozenset(
            (q, r) for q in range(-R, R + 1) for r in range(-R, R + 1) if on_board(R, q, r)
        )
        self.nbr = {
            (q, r): tuple(
                (q + dq, r + dr) if on_board(R, q + dq, r + dr) else None for dq, dr in DIRS
            )
            for q, r in self.cells
        }
        self.home = {"A": frozenset(row(R, -R)), "B": frozenset(row(R, R))}
        # celle dei bordi laterali subito accanto agli angoli della casa nemica
        wide = {"A": {(-R, R - 1), (1, R - 1)}, "B": {(R, 1 - R), (-1, 1 - R)}}
        self.goal = {
            p: self.home[other(p)] | (frozenset(wide[p]) if goal_wide else frozenset())
            for p in PLAYERS
        }


@lru_cache(maxsize=None)
def geometry(R, goal_wide=False):
    return Geometry(R, goal_wide)


def _centered(cells, n):
    off = (len(cells) - n) // 2
    return cells[off:off + n]


def initial_board(R, N):
    """Posizione iniziale come dict (q, r) -> 'A'|'B'.

    A riempie la fila r=-R al centro, l'eccesso va nella fila r=-R+1 al centro.
    B è l'immagine di A per riflessione nel centro (q, r) -> (-q, -r): così il campo
    è simmetrico per costruzione anche quando la centratura non è esatta.
    """
    first = min(N, R + 1)
    a_cells = _centered(row(R, -R), first) + _centered(row(R, -R + 1), N - first)
    board = {c: "A" for c in a_cells}
    board.update({(-q, -r): "B" for q, r in a_cells})
    return board
