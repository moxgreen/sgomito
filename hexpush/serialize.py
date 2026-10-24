"""Stato di gioco in forma JSON per il frontend web.

Il browser non conosce le regole: riceve le mosse legali già con la cella d'arrivo
(`to`) e quella dove finisce la pedina spinta (`pushed_to`, None oppure "off" se esce
dal campo con push_off), le mosse vietate dal ko o da no_pushback, le case e i traguardi.
Solo stdlib: hexpush resta senza dipendenze.
"""
import dataclasses

from .game import PASS, GameState, goal_counts, legal_moves


def cell_list(cells):
    """Celle come liste [q, r] in ordine stabile (prima r, poi q)."""
    return [[q, r] for q, r in sorted(cells, key=lambda c: (c[1], c[0]))]


def move_targets(geo, move):
    """(to, pushed_to) di una mossa: to = [q, r] dove arriva la pedina che muove;
    pushed_to = None se non è una spinta, "off" se la pedina spinta esce dal campo,
    altrimenti [q, r]."""
    dst = geo.nbr[(move.q, move.r)][move.d]
    pushed_to = None
    if move.push:
        behind = geo.nbr[dst][move.d]
        pushed_to = "off" if behind is None else list(behind)
    return list(dst), pushed_to


def move_to_dict(geo, move, player=None):
    if move is PASS:
        out = {"pass": True}
    else:
        to, pushed_to = move_targets(geo, move)
        out = {"q": move.q, "r": move.r, "d": move.d, "push": move.push,
               "to": to, "pushed_to": pushed_to}
    if player is not None:
        out["player"] = player
    return out


def ko_forbidden(state):
    """Mosse che sarebbero legali senza il ko e senza no_pushback e che una delle due
    regole vieta (lista di Move)."""
    if state.result is not None or not (state.rules.ko or state.rules.no_pushback):
        return []
    free = GameState(dataclasses.replace(state.rules, ko=False, no_pushback=False),
                     state.board, state.to_move, state.ply, state.history, state.turn_keys,
                     state.moves_left, state.locked, state.result, state.reason,
                     state.last_move)
    allowed = set(legal_moves(state))
    return [m for m in legal_moves(free) if m is not PASS and m not in allowed]


def forbidden_by(state, move):
    """Quale regola vieta una mossa di ko_forbidden: 'pushback' o 'ko'."""
    if (move.q, move.r, move.d) in state.banned:
        dst = state.geo.nbr[(move.q, move.r)][move.d]
        if state.board[dst] != state.to_move:
            return "pushback"
    return "ko"


def state_to_dict(state):
    """Tutto ciò che serve per disegnare `state` e scegliere una mossa.
    Se l'unica mossa è il passo, `legal` è vuota e `must_pass` è True."""
    geo = state.geo
    legal = legal_moves(state)
    counts = {"A": 0, "B": 0}
    for owner in state.board.values():
        counts[owner] += 1
    return {
        "rules": dataclasses.asdict(state.rules),
        "cells": cell_list(geo.cells),
        "home": {p: cell_list(cs) for p, cs in geo.home.items()},
        "goal": {p: cell_list(cs) for p, cs in geo.goal.items()},
        "board": [[q, r, p] for (q, r), p in sorted(state.board.items(),
                                                    key=lambda it: (it[0][1], it[0][0]))],
        "to_move": state.to_move,
        "moves_left": state.moves_left,
        "locked": list(state.locked) if state.locked is not None else None,
        "ply": state.ply,
        "legal": [move_to_dict(geo, m) for m in legal if m is not PASS],
        "must_pass": legal == [PASS],
        "ko_forbidden": [dict(move_to_dict(geo, m), rule=forbidden_by(state, m))
                         for m in ko_forbidden(state)],
        "pieces": counts,
        "reached": goal_counts(state),
        "last_move": None if state.last_move is None
        else move_to_dict(geo, state.last_move),
        "result": state.result,
        "reason": state.reason,
    }
