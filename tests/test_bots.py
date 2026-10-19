import random
import unittest

from hexpush.bots import BOTS, make_bot, winning_replies
from hexpush.board import geometry
from hexpush.game import (PASS, Move, apply, from_board, immediate_outcomes, legal_moves,
                          new_game)
from hexpush.mcts import FastBoard, fast_geometry, replay, terminal_value
from hexpush.rules import Ruleset

CONFIGS = [Ruleset(), Ruleset(R=3, N=3, K=2), Ruleset(R=5, N=8, goal_wide=True),
           Ruleset(push_own=False, ko=True), Ruleset(ko=True, K=2, goal_wide=True),
           Ruleset(push_off=True, ko=True), Ruleset(R=3, N=3, K=2, push_off=True),
           Ruleset(moves_per_turn=2), Ruleset(moves_per_turn=2, ko=True),
           Ruleset(R=3, N=3, moves_per_turn=2, ko=True, push_off=True)]


def as_moves(fb):
    cells = fb.geo.cells
    return {Move(*cells[src], d, kind >= 2) for src, d, kind in fb.moves()}


class TestFastBoard(unittest.TestCase):
    def test_same_rules_as_reference(self):
        """Partite casuali giocate in parallelo sui due motori: stesse mosse, stessi campi."""
        rng = random.Random(7)
        for rules in CONFIGS:
            for _ in range(40):
                s = new_game(rules)
                fb = FastBoard(s)
                while s.result is None:
                    ref = legal_moves(s)
                    if ref == [PASS]:
                        self.assertEqual(fb.moves(), [])
                        move = PASS
                        fb.pass_turn()
                        w = 0
                    else:
                        self.assertEqual(as_moves(fb), set(ref))
                        move = rng.choice(ref)
                        g = fb.geo
                        src = g.index[(move.q, move.r)]
                        kind = fb.is_legal(src, move.d)
                        w = fb.play(src, move.d, kind)
                    s = apply(s, move)
                    board = {fb.geo.cells[i]: "AB"[p - 1] for i, p in enumerate(fb.board) if p}
                    self.assertEqual(board, s.board)
                    if s.reason in ("goal", "elimination"):
                        self.assertEqual("AB"[w - 1], s.result)
                    else:
                        self.assertEqual(w, 0)
                    # anche ricostruendo da zero (ko compreso) si ottiene lo stesso
                    if s.result is None:
                        self.assertEqual(as_moves(FastBoard(s)), set(legal_moves(s)) - {PASS})

    def test_playout_ends(self):
        rng = random.Random(3)
        for rules in CONFIGS:
            v = FastBoard(new_game(rules)).random_playout(rng)
            self.assertIn(v, (0.0, 0.5, 1.0))
            v = FastBoard(new_game(rules)).random_playout(rng, limit=10)
            self.assertTrue(0.0 <= v <= 1.0)

    def test_winning_move_matches_reference(self):
        rng = random.Random(11)
        for rules in CONFIGS:
            for _ in range(20):
                s = new_game(rules)
                while s.result is None:
                    moves = legal_moves(s)
                    wins = {m for m, o in zip(moves, immediate_outcomes(s, moves)) if o == 1}
                    found = FastBoard(s).winning_move()
                    if wins:
                        cells = fast_geometry(rules.R, rules.goal_wide).cells
                        src, d, kind = found
                        self.assertIn(Move(*cells[src], d, kind >= 2), wins)
                    else:
                        self.assertIsNone(found)
                    s = apply(s, rng.choice(moves))

    def test_tree_bookkeeping_matches_reference(self):
        """Hash, ripetizioni e limite di semimosse dell'albero veloce: si gioca una
        partita di riferimento e, da ogni posizione, si rigioca una discesa casuale
        nell'albero confrontando l'esito con game.apply."""
        rng = random.Random(5)
        configs = [Ruleset(R=3, N=2, max_plies=60), Ruleset(R=3, N=2, ko=True, max_plies=60),
                   Ruleset(R=4, N=3, push_own=False, max_plies=40),
                   Ruleset(R=3, N=3, push_off=True, max_plies=60),
                   Ruleset(R=3, N=2, moves_per_turn=2, max_plies=60),
                   Ruleset(R=3, N=3, moves_per_turn=2, ko=True, max_plies=60)]
        reasons = set()
        for rules in configs:
            geo = fast_geometry(rules.R, rules.goal_wide)
            for _ in range(15):
                s = new_game(rules)
                while s.result is None:
                    history = {}
                    for key in s.history:
                        h = geo.hash_key(key)
                        history[h] = history.get(h, 0) + 1
                    fb, path, ref = FastBoard(s), {}, s
                    while ref.result is None:
                        ms = fb.moves() or [None]
                        fm = rng.choice(ms)
                        rm = None if fm is None else Move(*geo.cells[fm[0]], fm[1], fm[2] >= 2)
                        winner = replay(fb, fm)
                        path[fb.hash] = path.get(fb.hash, 0) + 1
                        ref = apply(ref, rm)
                        self.assertEqual(fb.hash, geo.hash_key(ref.key))
                        value = terminal_value(fb, winner, history, path)
                        expected = {None: None, "A": 1.0, "B": 0.0, "draw": 0.5}[ref.result]
                        self.assertEqual(value, expected)
                        reasons.add(ref.reason)
                    s = apply(s, rng.choice(legal_moves(s)))
        self.assertTrue({"goal", "repetition", "max_plies", "elimination"} <= reasons)

    def test_fast_geometry(self):
        for R in (3, 4, 5):
            self.assertEqual(fast_geometry(R, False).n, len(geometry(R).cells))


class TestBots(unittest.TestCase):
    def test_all_bots_play_legal_moves(self):
        for name in BOTS:
            spec = f"{name}:30" if name.startswith("mcts") else name
            for rules in CONFIGS[:2] + CONFIGS[3:4] + CONFIGS[-2:]:
                rng = random.Random(1)
                s = new_game(rules)
                bots = {"A": make_bot(spec, rng), "B": make_bot("random", rng)}
                while s.result is None:
                    m = bots[s.to_move].choose(s)
                    self.assertIn(m, legal_moves(s), name)
                    s = apply(s, m)

    def test_greedy_takes_immediate_win(self):
        s = from_board(Ruleset(), {(0, 3): "A", (2, 0): "A", (0, -1): "B"})
        for seed in range(10):
            m = make_bot("greedy", random.Random(seed)).choose(s)
            self.assertEqual(apply(s, m).result, "A")

    def test_greedy_avoids_pushing_opponent_home(self):
        # A in (1,-2) potrebbe spingere B su (1,-4), cioè farlo vincere: punteggio -inf
        s = from_board(Ruleset(), {(1, -2): "A", (1, -3): "B"})
        for seed in range(10):
            m = make_bot("greedy", random.Random(seed)).choose(s)
            self.assertNotEqual(m, Move(1, -2, 3, True))

    def test_mcts_finds_immediate_win(self):
        s = from_board(Ruleset(), {(0, 3): "A", (-3, 0): "A", (0, -1): "B", (1, -1): "B"})
        m = make_bot("mcts:200", random.Random(0)).choose(s)
        self.assertEqual(apply(s, m).result, "A")

    def test_mcts_blocks_immediate_loss(self):
        # B in (0,-3) minaccia (1,-4); A in (0,-4) deve rispingerla verso (0,-2)
        s = from_board(Ruleset(), {(0, -4): "A", (2, 1): "A", (0, -3): "B", (-2, 3): "B"})
        for seed in range(3):
            m = make_bot("mcts:500", random.Random(seed)).choose(s)
            self.assertEqual(winning_replies(apply(s, m)), 0, m)


if __name__ == "__main__":
    unittest.main()
