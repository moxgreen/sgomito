"""Allenamento della policy (hexpush/policy.py): gradiente della perdita e passaggio dal
corpus alle posizioni. La parte JavaScript (feature, PUCT) è in test_js_engine.py."""
import random
import unittest

from hexpush.game import PASS, apply, legal_moves, new_game
from hexpush.policy import position_loss, position_rows
from hexpush.rules import Ruleset


class TestPolicy(unittest.TestCase):
    def test_gradient(self):
        """Il gradiente di position_loss è quello delle differenze finite."""
        rng = random.Random(1)
        dim = 6
        row = {"visits": [rng.randint(0, 30) for _ in range(5)],
               "f": [[[k, rng.choice([1, 2, 0.5])] for k in range(dim) if rng.random() < 0.5]
                     for _ in range(5)]}
        row["visits"][0] += 1
        w = [rng.uniform(-1, 1) for _ in range(dim)]
        grad = [0.0] * dim
        position_loss(w, row, grad)
        eps = 1e-6
        for k in range(dim):
            hi, lo = list(w), list(w)
            hi[k] += eps
            lo[k] -= eps
            num = (position_loss(hi, row)[0] - position_loss(lo, row)[0]) / (2 * eps)
            self.assertAlmostEqual(grad[k], num, places=5)

    def test_position_rows(self):
        """Le posizioni del corpus si ricostruiscono rigiocando le mosse; quelle senza
        ricerca (mossa obbligata o vincente) si saltano."""
        rules = Ruleset(ko=True, moves_per_turn=2)
        rng = random.Random(4)
        s = new_game(rules)
        moves, visits, plies = [], [], []
        while s.result is None and s.ply < 20:
            m = rng.choice(legal_moves(s))
            searched = s.ply % 4 != 1
            if searched:
                plies.append(s.ply)
            visits.append([[m.q, m.r, m.d, 7, 0.5]] if searched else None)
            moves.append(None if m is PASS else [m.q, m.r, m.d])
            s = apply(s, m)
        rows = list(position_rows({"moves": moves, "visits": visits}, rules))
        self.assertEqual([st.ply for st, _ in rows], plies)
        for (st, vis), m in zip(rows, (m for m, v in zip(moves, visits) if v)):
            self.assertEqual(vis, {tuple(m): 7})


if __name__ == "__main__":
    unittest.main()
