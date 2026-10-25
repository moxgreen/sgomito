"""Coerenza del motore JavaScript del browser (web/play/static/play/mcts.js) con quello di
riferimento: geometria, mosse legali, vittorie immediate, ko, no_pushback, ripetizioni e
limite di semimosse come li conta l'albero, e ricerca che sceglie mosse sensate.

I casi si preparano qui con game.py e li controlla tests/js/check_engine.mjs. Serve node
(almeno la versione 22); senza, il test si salta e il resto resta solo stdlib.
"""
import dataclasses
import json
import random
import shutil
import subprocess
import unittest
from pathlib import Path

from hexpush.board import DIRS, OPPOSITE, geometry, other
from hexpush.bots import winning_replies
from hexpush.game import PASS, apply, from_board, immediate_outcomes, legal_moves, new_game
from hexpush.mcts import POLICIES, FastBoard, MCTSDecisiveBot, TimePolicy
from hexpush.rules import Ruleset
from hexpush.serialize import cell_list, key_to_list, search_state_to_dict

from .test_bots import CONFIGS

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tests" / "js" / "check_engine.mjs"
NODE = shutil.which("node")

# come test_tree_bookkeeping in test_bots.py: partite corte, per arrivare anche alle
# patte per ripetizione e per limite di semimosse
SHORT = [Ruleset(R=3, N=2, max_plies=60), Ruleset(R=3, N=2, ko=True, max_plies=60),
         Ruleset(R=4, N=3, push_own=False, max_plies=40),
         Ruleset(R=3, N=3, push_off=True, max_plies=60),
         Ruleset(R=3, N=2, moves_per_turn=2, max_plies=60),
         Ruleset(R=3, N=3, moves_per_turn=2, ko=True, no_pushback=True, max_plies=60),
         # una pedina sola si ripete spesso; con K = N una pedina buttata fuori elimina
         Ruleset(R=3, N=1, max_plies=80),
         Ruleset(R=3, N=2, K=2, push_off=True, moves_per_turn=2, ko=True, max_plies=80)]
CANDIDATE = Ruleset(ko=True, no_pushback=True, moves_per_turn=2)  # quella del sito
N_FEATURES = 38  # mcts.FEATURE_NAMES


def node_version():
    if NODE is None:
        return 0
    out = subprocess.run([NODE, "--version"], capture_output=True, text=True).stdout
    return int(out.strip().lstrip("v").split(".")[0])


def run_node(cases):
    proc = subprocess.run([NODE, str(SCRIPT)], input=json.dumps(cases), capture_output=True,
                          text=True, timeout=600)
    if proc.returncode != 0:
        raise AssertionError(f"node è uscito con {proc.returncode}:\n{proc.stderr}")
    return json.loads(proc.stdout)


def mv(m):
    return [m.q, m.r, m.d]


def game_case(rules, rng, label, snapshot_every=9):
    """Partita casuale con tutto ciò che il JS deve ritrovare a ogni semimossa."""
    s = new_game(rules)
    case = {"kind": "game", "label": label, "start": search_state_to_dict(s), "steps": []}
    while s.result is None:
        moves = legal_moves(s)
        wins = [m for m, o in zip(moves, immediate_outcomes(s, moves)) if o == 1]
        move = rng.choice(moves)
        s = apply(s, move)
        board, to_move, locked, moves_left = key_to_list(s.key)
        step = {"legal": [[m.q, m.r, m.d, m.push] for m in moves if m is not PASS],
                "wins": [mv(m) for m in wins],
                "move": None if move is PASS else mv(move),
                "result": s.result, "board": board, "to_move": to_move,
                "moves_left": moves_left, "locked": locked}
        if s.result is None and len(case["steps"]) % snapshot_every == 0:
            step["state"] = search_state_to_dict(s)
        case["steps"].append(step)
    return case


def search_case(s, ok, label, n_sim=300, seed=0):
    return {"kind": "search", "label": label, "state": search_state_to_dict(s),
            "ok": [mv(m) for m in ok], "n_sim": n_sim, "seed": seed}


@unittest.skipIf(node_version() < 22, "serve node >= 22 per il motore JavaScript")
class TestJsEngine(unittest.TestCase):
    def assert_node_ok(self, cases):
        out = run_node(cases)
        self.assertEqual(out["errors"], [])
        self.assertGreater(out["checked"], 0)

    def test_geometry(self):
        cases = []
        for R in (3, 4, 5):
            for wide in (False, True):
                geo = geometry(R, wide)
                cases.append({"kind": "geometry", "R": R, "goal_wide": wide,
                              "cells": cell_list(geo.cells),
                              "goal": {p: cell_list(cs) for p, cs in geo.goal.items()}})
        self.assert_node_ok(cases)

    def test_same_rules_as_reference(self):
        rng = random.Random(7)
        cases = [game_case(rules, rng, f"{rules} #{i}")
                 for rules in CONFIGS for i in range(6)]
        self.assert_node_ok(cases)

    def test_tree_bookkeeping(self):
        """Ripetizioni e limite di semimosse: tutti gli esiti devono comparire."""
        rng = random.Random(5)
        cases, reasons = [], set()
        for rules in SHORT:
            for i in range(25):
                case = game_case(rules, rng, f"{rules} #{i}", snapshot_every=4)
                cases.append(case)
                s = new_game(rules)
                for st in case["steps"]:
                    s = apply(s, PASS if st["move"] is None else next(
                        m for m in legal_moves(s) if m is not PASS and mv(m) == st["move"]))
                reasons.add(s.reason)
        self.assertTrue({"goal", "repetition", "max_plies", "elimination"} <= reasons)
        self.assert_node_ok(cases)

    def test_search(self):
        cases = []
        # vittoria immediata
        s = from_board(Ruleset(), {(0, 3): "A", (-3, 0): "A", (0, -1): "B", (1, -1): "B"})
        moves = legal_moves(s)
        wins = [m for m, o in zip(moves, immediate_outcomes(s, moves)) if o == 1]
        cases.append(search_case(s, wins, "vittoria immediata"))
        # B in (0,-3) minaccia (1,-4); A in (0,-4) deve rispingerla
        s = from_board(Ruleset(), {(0, -4): "A", (2, 1): "A", (0, -3): "B", (-2, 3): "B"})
        safe = [m for m in legal_moves(s) if winning_replies(apply(s, m)) == 0]
        for seed in range(3):
            cases.append(search_case(s, safe, "difesa", n_sim=500, seed=seed))
        # posizioni qualsiasi con le regole del sito: una mossa legale, a metà turno compreso
        rng = random.Random(2)
        s = new_game(CANDIDATE)
        while s.result is None and s.ply < 40:
            if s.ply % 3 == 0:
                cases.append(search_case(s, legal_moves(s), f"partita ply {s.ply}", n_sim=200,
                                         seed=s.ply))
            s = apply(s, rng.choice(legal_moves(s)))
        self.assert_node_ok(cases)

    def test_tree_reuse(self):
        """Ricerche con lo stesso keep lungo partite casuali (una e due mosse per turno):
        il sottoalbero ereditato deve essere quello della posizione nuova. Le posizioni
        `fresh` (di nuovo l'inizio, o un'altra partita) non devono riusare nulla."""
        rng = random.Random(11)
        cases = []
        for rules in (CANDIDATE, Ruleset(ko=True), SHORT[5], SHORT[3]):
            steps = []
            s = new_game(rules)
            while s.result is None and s.ply < 30:
                steps.append({"state": search_state_to_dict(s),
                              "legal": [None if m is PASS else mv(m) for m in legal_moves(s)]})
                s = apply(s, rng.choice(legal_moves(s)))
            first = dict(steps[0], fresh=True)
            steps += [first, first]  # stessa posizione, stessa semimossa: nessun riuso
            cases.append({"kind": "reuse", "label": str(rules), "steps": steps, "n_sim": 300,
                          "min_reused": 5})
        self.assert_node_ok(cases)

    def test_time_policy(self):
        """Stesse decisioni di TimePolicy.stop, su statistiche vere (prese a metà di
        ricerche reali) e inventate, comprese parità di visite e di valori."""
        rng = random.Random(9)
        policies = list(POLICIES.values()) + [TimePolicy(early=0.3, extend=0.8, min_factor=0.1,
                                                         max_factor=2.0)]
        snapshots = []
        s = new_game(CANDIDATE)
        while s.result is None and s.ply < 30:
            bot = MCTSDecisiveBot(random.Random(s.ply), 1000, policy=_Recorder(snapshots))
            s = apply(s, bot.choose(s))
        for _ in range(300):
            n = rng.randint(1, 6)
            stats = [(v, rng.choice([0.0, 0.5, 1.0, v / 2, v * rng.random()]))
                     for v in (rng.randint(0, 50) for _ in range(n))]
            snapshots.append((rng.choice([0, 63, 127, 500, 1000, 2000]), stats))
        tests = []
        for done, stats in snapshots:
            for policy in policies:
                budget = rng.choice([1000, 777.5, 2000])
                tests.append({"policy": dataclasses.asdict(policy), "done": done,
                              "budget": budget, "stats": stats,
                              "stop": policy.stop(done, budget, stats)})
        self.assertTrue(any(t["stop"] for t in tests) and not all(t["stop"] for t in tests))
        # fattore di fase: stesse distanze dal traguardo lungo una partita
        scales = []
        phased = [p for p in policies if p.phase]
        self.assertTrue(phased)
        s = new_game(CANDIDATE)
        while s.result is None:
            for policy in phased:
                scales.append({"policy": dataclasses.asdict(policy),
                               "state": search_state_to_dict(s),
                               "scale": policy.scale(FastBoard(s))})
            s = apply(s, rng.choice(legal_moves(s)))
        self.assertGreater(len({sc["scale"] for sc in scales}), 2)
        cases = [{"kind": "policy", "tests": tests, "scales": scales}]
        # ricerche vere con le politiche: simulazioni nei limiti
        s = new_game(CANDIDATE)
        rng = random.Random(4)
        for _ in range(12):
            s = apply(s, rng.choice(legal_moves(s)))
        for name, policy in POLICIES.items():
            budget = policy.scale(FastBoard(s)) * 1000
            hi = policy.max_factor * budget if policy.extend else budget
            case = search_case(s, legal_moves(s), f"politica {name}", n_sim=1000)
            case.update(policy=dataclasses.asdict(policy), sims=[policy.min_factor * budget, hi])
            cases.append(case)
        self.assert_node_ok(cases)


def reference_features(state, move):
    """Feature di mcts.moveFeatures ricalcolate con game.py: le minacce con legal_moves e
    immediate_outcomes sulle posizioni dopo la mossa. Vale con no_pushback spento e due
    mosse per turno, dove la legalità semplificata del JS coincide con quella vera."""
    rules, geo = state.rules, state.geo
    me, opp = state.to_move, other(state.to_move)
    R = rules.R
    nbr = geo.nbr
    src = (move.q, move.r)
    dst = nbr[src][move.d]

    def dist(p, cell):
        return R - cell[1] if p == "A" else cell[1] + R

    def pushers(board, x, o):
        n = 0
        for e in range(6):
            y, behind = nbr[x][e], nbr[x][OPPOSITE[e]]
            n += y is not None and board.get(y) == o and behind is not None and behind not in board
        return n

    def wins(board, p, locked=None, cap=1):
        s = from_board(rules, board, p)
        if s.result is not None:
            return None
        n = sum(o == 1 for m, o in zip(legal_moves(s), immediate_outcomes(s, legal_moves(s)))
                if m is not PASS and (m.q, m.r) != locked)
        return min(n, cap)

    f = dict.fromkeys(["push_own", "push_enemy", "own_pushed_fwd", "enemy_back", "enemy_side",
                       "enemy_fwd", "enemy_d1", "enemy_d2", "enemy_d3p", "adj_enemy",
                       "adj_own", "loses"], 0)
    dr = DIRS[move.d][1] * (1 if me == "A" else -1)
    for name, flag in (("dir_fwd", dr > 0), ("dir_side", dr == 0), ("dir_back", dr < 0)):
        f[name] = int(flag)
    for k in range(1, 9):
        f[f"src_d{k}"] = int(min(max(dist(me, src), 1), 8) == k)
    for k in range(9):
        f[f"dst_d{k}"] = int(min(max(dist(me, dst), 0), 8) == k)
    f["vul_src"] = pushers(state.board, src, opp)
    if move.push:
        if state.board[dst] == me:
            f["push_own"], f["own_pushed_fwd"] = 1, int(dr > 0)
        else:
            f["push_enemy"] = 1
            f["enemy_back" if dr > 0 else "enemy_fwd" if dr < 0 else "enemy_side"] = 1
            ed = dist(opp, dst)
            f["enemy_d1" if ed <= 1 else "enemy_d2" if ed == 2 else "enemy_d3p"] = 1
    after = apply(state, move)
    board = after.board
    f["loses"] = int(any(owner == opp and cell in geo.goal[opp] for cell, owner in board.items()))
    for y in nbr[dst]:
        if y is not None and y in board:
            f["adj_enemy" if board[y] == opp else "adj_own"] += y != dst
    f["vul_dst"] = pushers(board, dst, opp)
    if after.result is not None:
        return f  # partita finita: le minacce non si confrontano
    if state.moves_left == 1:
        f["end_opp_win"] = wins(board, opp)
        f["end_my_threats"] = wins(board, me, cap=2)
        f["mid_my_win"] = f["mid_opp_win"] = 0
    else:
        moves = legal_moves(after)
        f["mid_my_win"] = int(any(o == 1 for o in immediate_outcomes(after, moves)))
        f["mid_opp_win"] = wins(board, opp)
        f["end_opp_win"] = f["end_my_threats"] = 0
    return f


@unittest.skipIf(node_version() < 22, "serve node >= 22 per il motore JavaScript")
class TestJsPrior(unittest.TestCase):
    """Policy e PUCT di mcts.js (opzione prior, spenta di default)."""

    def assert_node_ok(self, cases):
        out = run_node(cases)
        self.assertEqual(out["errors"], [])
        self.assertGreater(out["checked"], 0)

    def test_features(self):
        rng = random.Random(11)
        cases, seen = [], set()
        for rules in (Ruleset(ko=True, no_pushback=False, moves_per_turn=2),
                      Ruleset(R=3, N=3, ko=True, no_pushback=False, moves_per_turn=2)):
            for g in range(12):
                s = new_game(rules)
                while s.result is None:
                    moves = [m for m in legal_moves(s) if m is not PASS]
                    if moves and s.ply % 3 == g % 3:
                        expect = []
                        for m in moves:
                            values = reference_features(s, m)
                            seen.update(k for k, v in values.items() if v)
                            expect.append({"move": mv(m), "values": values})
                        cases.append({"kind": "features", "label": f"{rules} ply {s.ply}",
                                      "state": search_state_to_dict(s), "expect": expect,
                                      "n_features": N_FEATURES,
                                      "weights": [rng.uniform(-2, 2)
                                                  for _ in range(N_FEATURES)],
                                      "temp": rng.choice([0.5, 1, 2])})
                    s = apply(s, rng.choice(legal_moves(s)))
        # le minacce devono comparire davvero nelle posizioni controllate
        self.assertTrue({"end_opp_win", "end_my_threats", "mid_my_win", "mid_opp_win", "loses",
                         "push_enemy", "enemy_d1", "own_pushed_fwd", "vul_dst"} <= seen,
                        seen)
        self.assert_node_ok(cases)

    def test_tree_reuse(self):
        """Riuso dell'albero (keep) con il prior, come TestJsEngine.test_tree_reuse; un
        albero UCT non si riusa in una ricerca col prior."""
        rng = random.Random(12)
        cases = []
        for rules in (CANDIDATE, Ruleset(ko=True)):
            steps = []
            s = new_game(rules)
            while s.result is None and s.ply < 30:
                steps.append({"state": search_state_to_dict(s),
                              "legal": [None if m is PASS else mv(m) for m in legal_moves(s)]})
                s = apply(s, rng.choice(legal_moves(s)))
            first = dict(steps[0], fresh=True)
            steps += [first, first]
            cases.append({"kind": "reuse", "label": f"prior {rules}", "steps": steps,
                          "n_sim": 300, "min_reused": 5, "opts": {"prior": True}})
        self.assert_node_ok(cases)

    def test_search(self):
        """Con il prior: vittorie immediate, la difesa, posizioni qualsiasi; visite e
        determinismo come la ricerca pura (checkSearch)."""
        cases = []
        flat = {"prior": True, "weights": [0.0] * N_FEATURES}
        s = from_board(Ruleset(), {(0, 3): "A", (-3, 0): "A", (0, -1): "B", (1, -1): "B"})
        moves = legal_moves(s)
        wins = [m for m, o in zip(moves, immediate_outcomes(s, moves)) if o == 1]
        cases.append(dict(search_case(s, wins, "vittoria immediata"), opts={"prior": True}))
        s = from_board(Ruleset(), {(0, -4): "A", (2, 1): "A", (0, -3): "B", (-2, 3): "B"})
        safe = [m for m in legal_moves(s) if winning_replies(apply(s, m)) == 0]
        for seed in range(3):
            for opts in ({"prior": True}, dict(flat, cPuct=3.0, fpu=0.2)):
                cases.append(dict(search_case(s, safe, "difesa", n_sim=500, seed=seed),
                                  opts=opts))
        rng = random.Random(3)
        s = new_game(CANDIDATE)
        while s.result is None and s.ply < 40:
            if s.ply % 3 == 0:
                cases.append(dict(search_case(s, legal_moves(s), f"partita ply {s.ply}",
                                              n_sim=200, seed=s.ply),
                                  opts=dict(flat, priorTemp=2.0) if s.ply % 2 else
                                  {"prior": True}))
            s = apply(s, rng.choice(legal_moves(s)))
        self.assert_node_ok(cases)


class _Recorder(TimePolicy):
    """Politica che non ferma mai la ricerca e ne annota le statistiche."""

    def __init__(self, out):
        super().__init__()
        object.__setattr__(self, "out", out)

    def stop(self, done, budget, stats):
        self.out.append((done, list(stats)))
        return False


if __name__ == "__main__":
    unittest.main()
