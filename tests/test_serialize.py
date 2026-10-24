import json
import unittest

from hexpush.board import geometry
from hexpush.game import PASS, Move, apply, from_board, legal_moves, new_game
from hexpush.rules import Ruleset
from hexpush.serialize import ko_forbidden, move_targets, move_to_dict, state_to_dict

E, W, SE, NW, NE, SW = range(6)


class TestMoveTargets(unittest.TestCase):
    def test_simple_move(self):
        geo = geometry(4)
        self.assertEqual(move_targets(geo, Move(0, 0, NE, False)), ([1, -1], None))

    def test_push(self):
        geo = geometry(4)
        self.assertEqual(move_targets(geo, Move(0, 0, E, True)), ([1, 0], [2, 0]))

    def test_push_off(self):
        geo = geometry(4)
        # (3,0) spinge (4,0) verso est: dietro non c'è campo
        self.assertEqual(move_targets(geo, Move(3, 0, E, True)), ([4, 0], "off"))

    def test_pass(self):
        self.assertEqual(move_to_dict(geometry(4), PASS, "A"), {"pass": True, "player": "A"})


class TestKoForbidden(unittest.TestCase):
    def test_push_back_is_forbidden(self):
        rules = Ruleset(ko=True)
        s = from_board(rules, {(0, 0): "A", (1, 0): "B"}, "A")
        s = apply(s, Move(0, 0, E, True))          # A (1,0), B (2,0)
        back = Move(2, 0, W, True)                 # ricreerebbe la posizione iniziale
        self.assertNotIn(back, legal_moves(s))
        self.assertEqual(ko_forbidden(s), [back])

    def test_pushback_is_forbidden(self):
        rules = Ruleset(moves_per_turn=2, no_pushback=True)
        cells = {(0, 0): "A", (0, -2): "A", (1, 0): "B"}
        s = from_board(rules, cells, "A", moves_left=1)
        s = apply(s, Move(0, 0, E, True))          # A (1,0), B (2,0): tocca a B
        back = Move(2, 0, W, True)
        self.assertEqual(ko_forbidden(s), [back])
        self.assertEqual(state_to_dict(s)["ko_forbidden"][0]["rule"], "pushback")

    def test_no_ko_rule(self):
        s = from_board(Ruleset(no_pushback=False), {(0, 0): "A", (1, 0): "B"}, "A")
        s = apply(s, Move(0, 0, E, True))
        self.assertEqual(ko_forbidden(s), [])


class TestStateToDict(unittest.TestCase):
    def test_initial_state(self):
        s = new_game(Ruleset(ko=True, moves_per_turn=2))
        d = state_to_dict(s)
        json.dumps(d)  # dev'essere serializzabile così com'è
        self.assertEqual(d["to_move"], "A")
        self.assertEqual(d["moves_left"], 1)
        self.assertIsNone(d["locked"])
        self.assertEqual(len(d["cells"]), 61)
        self.assertEqual(len(d["board"]), 10)
        self.assertEqual(d["home"]["A"], [[0, -4], [1, -4], [2, -4], [3, -4], [4, -4]])
        self.assertEqual(d["goal"]["A"], d["home"]["B"])
        self.assertEqual(len(d["legal"]), len(legal_moves(s)))
        self.assertFalse(d["must_pass"])
        self.assertIsNone(d["result"])
        for m in d["legal"]:
            mv = Move(m["q"], m["r"], m["d"], m["push"])
            self.assertEqual([m["to"], m["pushed_to"]], list(move_targets(s.geo, mv)))

    def test_locked_and_last_move(self):
        s = new_game(Ruleset(ko=True, moves_per_turn=2))
        s = apply(s, legal_moves(s)[0])            # A apre con una mossa: tocca a B
        m = legal_moves(s)[0]
        s = apply(s, m)
        d = state_to_dict(s)
        self.assertEqual(d["to_move"], "B")
        self.assertEqual(d["moves_left"], 1)
        to = d["last_move"]["to"]
        self.assertEqual(d["locked"], to)
        self.assertNotIn(to, [[x["q"], x["r"]] for x in d["legal"]])

    def test_goal_wide_and_finished(self):
        rules = Ruleset(goal_wide=True)
        d = state_to_dict(new_game(rules))
        self.assertEqual(len(d["goal"]["A"]), 7)
        s = from_board(rules, {(0, 3): "A", (0, -3): "B"}, "A")
        s = apply(s, Move(0, 3, SE, False))
        d = state_to_dict(s)
        self.assertEqual((d["result"], d["reason"]), ("A", "goal"))
        self.assertEqual(d["legal"], [])
        self.assertEqual(d["reached"], {"A": 1, "B": 0})


if __name__ == "__main__":
    unittest.main()
