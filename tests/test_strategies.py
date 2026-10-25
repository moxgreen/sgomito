import random
import unittest

from hexpush.board import DIRS
from hexpush.corpus import play
from hexpush.rules import Ruleset
from hexpush.strategies import (MIRROR_DIR, NAMES, canonical, ending, kmeans, player_features,
                                replay, silhouette)


def mirror_moves(moves):
    return [None if m is None else [-m[0] - m[1], m[1], MIRROR_DIR[m[2]], m[3]] for m in moves]


class TestStrategies(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rules = Ruleset(R=4, N=5, ko=True, moves_per_turn=2)
        cls.games = [play((rules, "greedy", "random", f"t-{i}")) for i in range(6)]

    def test_mirror_is_a_reflection(self):
        for d, (dq, dr) in enumerate(DIRS):
            self.assertEqual(MIRROR_DIR[MIRROR_DIR[d]], d)
            self.assertEqual(DIRS[MIRROR_DIR[d]], (-dq - dr, dr))

    def test_canonical_ignores_mirror_image(self):
        for g in self.games:
            self.assertEqual(canonical(g["moves"]), canonical(mirror_moves(g["moves"])))

    def test_mirrored_game_is_legal_and_ends_the_same(self):
        g = self.games[0]
        m = dict(g, moves=mirror_moves(g["moves"]))
        self.assertEqual(replay(m)[-1].result, replay(g)[-1].result)

    def test_features_in_range(self):
        for g in self.games:
            st = replay(g)
            self.assertEqual(st[-1].result, g["result"])
            feats = player_features(g, st)
            for p in "AB":
                self.assertEqual(set(feats[p]), set(NAMES))
                for k, v in feats[p].items():
                    if k != "tempo":
                        self.assertTrue(0 <= v <= 1, (k, v))
            e = ending(g, st)
            if g["result"] in "AB":
                self.assertIn(e["come"], ("passo", "spinta", "staffetta"))
                self.assertGreaterEqual(e["pronte"], 1 if e["mosse_finali"] == 1 else 0)

    def test_kmeans_finds_separated_groups(self):
        rng = random.Random(1)
        X = [[rng.gauss(c, 0.1), rng.gauss(c, 0.1)] for c in (0, 5) for _ in range(20)]
        _, lab = kmeans(X, 2, rng)
        self.assertEqual(len(set(lab[:20])), 1)
        self.assertNotEqual(lab[0], lab[20])
        D = [[sum((a - b) ** 2 for a, b in zip(x, y)) ** 0.5 for y in X] for x in X]
        self.assertGreater(silhouette(D, lab, 2), 0.9)


if __name__ == "__main__":
    unittest.main()
