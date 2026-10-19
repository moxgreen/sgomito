import dataclasses
import random
import unittest

from hexpush.board import DIRS, OPPOSITE, geometry, initial_board, row
from hexpush.game import (PASS, Move, apply, from_board, legal_moves, new_game, render,
                          winner)
from hexpush.rules import Ruleset

E, W, SE, NW, NE, SW = range(6)


def board_of(**cells):
    """board_of(A=[(0,0)], B=[(1,0)]) -> {(0,0): 'A', (1,0): 'B'}"""
    return {c: p for p, cs in cells.items() for c in cs}


class TestRuleset(unittest.TestCase):
    def test_frozen(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            Ruleset().R = 5

    def test_invalid(self):
        for kwargs in ({"R": 1}, {"N": 0}, {"R": 3, "N": 10}, {"K": 0}, {"N": 3, "K": 4}):
            with self.assertRaises(ValueError, msg=kwargs):
                Ruleset(**kwargs)


class TestBoard(unittest.TestCase):
    def test_cell_count(self):
        for R, n in ((3, 37), (4, 61), (5, 91)):
            self.assertEqual(len(geometry(R).cells), n)

    def test_neighbors_are_symmetric(self):
        geo = geometry(4)
        for c in geo.cells:
            for d, t in enumerate(geo.nbr[c]):
                if t is not None:
                    self.assertEqual(geo.nbr[t][OPPOSITE[d]], c)
                    self.assertEqual((t[0] - c[0], t[1] - c[1]), DIRS[d])

    def test_homes_and_goals(self):
        for R in (3, 4, 5):
            geo = geometry(R)
            self.assertEqual(len(geo.home["A"]), R + 1)
            self.assertTrue(all(r == -R for _, r in geo.home["A"]))
            self.assertEqual(geo.goal["A"], geo.home["B"])
            self.assertEqual(geo.goal["B"], geo.home["A"])

    def test_goal_wide(self):
        for R in (3, 4, 5):
            geo = geometry(R, goal_wide=True)
            for p, enemy in (("A", "B"), ("B", "A")):
                extra = geo.goal[p] - geo.home[enemy]
                self.assertEqual(len(extra), 2)
                for c in extra:
                    self.assertIn(c, geo.cells)
                    # sul bordo, e adiacente a un angolo della casa nemica
                    self.assertIn(None, geo.nbr[c])
                    corners = {row(R, c[1] + (1 if p == "A" else -1))[i] for i in (0, -1)}
                    self.assertTrue(corners & set(geo.nbr[c]))

    def test_initial_positions(self):
        for R in (3, 4, 5):
            for N in range(1, 2 * R + 4):
                board = initial_board(R, N)
                a = [c for c, p in board.items() if p == "A"]
                b = [c for c, p in board.items() if p == "B"]
                self.assertEqual(len(a), N)
                self.assertEqual(len(b), N)
                # simmetria per riflessione nel centro
                self.assertEqual({(-q, -r) for q, r in a}, set(b))
                on_home = sum(1 for _, r in a if r == -R)
                self.assertEqual(on_home, min(N, R + 1))
                self.assertTrue(all(r in (-R, -R + 1) for _, r in a))

    def test_initial_centered(self):
        self.assertEqual(
            sorted(c for c, p in initial_board(4, 3).items() if p == "A"),
            [(1, -4), (2, -4), (3, -4)],
        )
        self.assertEqual(
            sorted(c for c, p in initial_board(4, 8).items() if p == "A" and c[1] == -3),
            [(0, -3), (1, -3), (2, -3)],
        )


class TestMoves(unittest.TestCase):
    def test_opening_moves(self):
        moves = legal_moves(new_game())
        # 5 pedine in fila: ognuna può solo scendere SE o SW, nessuna spinta possibile
        self.assertEqual(len(moves), 10)
        self.assertTrue(all(not m.push and m.d in (SE, SW) for m in moves))

    def test_simple_move_does_not_mutate(self):
        s = from_board(Ruleset(), board_of(A=[(0, 0)], B=[(2, -2)]))
        before = dict(s.board)
        t = apply(s, Move(0, 0, E, False))
        self.assertEqual(s.board, before)
        self.assertEqual(t.board, {(1, 0): "A", (2, -2): "B"})
        self.assertEqual(t.to_move, "B")
        self.assertEqual(t.ply, 1)

    def test_push_opponent(self):
        s = from_board(Ruleset(), board_of(A=[(0, 0)], B=[(1, 0)]))
        self.assertIn(Move(0, 0, E, True), legal_moves(s))
        t = apply(s, Move(0, 0, E, True))
        self.assertEqual(t.board, {(1, 0): "A", (2, 0): "B"})

    def test_push_against_wall_is_illegal(self):
        # (4,0) è sul bordo est: dietro non c'è niente
        s = from_board(Ruleset(), board_of(A=[(3, 0)], B=[(4, 0)]))
        self.assertNotIn(Move(3, 0, E, True), legal_moves(s))
        # un passo prima la spinta è legale
        s = from_board(Ruleset(), board_of(A=[(2, 0)], B=[(3, 0)]))
        self.assertIn(Move(2, 0, E, True), legal_moves(s))

    def test_push_with_occupied_cell_behind_is_illegal(self):
        for behind in ("A", "B"):
            s = from_board(Ruleset(), {(1, 0): "A", (2, 0): "B", (3, 0): behind})
            moves = legal_moves(s)
            self.assertNotIn(Move(1, 0, E, True), moves)
            self.assertFalse(any(m.d == E and (m.q, m.r) == (1, 0) for m in moves))

    def test_push_own(self):
        cells = board_of(A=[(1, 0), (2, 0)], B=[(-2, 2)])
        s = from_board(Ruleset(push_own=True), cells)
        self.assertIn(Move(1, 0, E, True), legal_moves(s))
        t = apply(s, Move(1, 0, E, True))
        self.assertEqual(t.board, {(2, 0): "A", (3, 0): "A", (-2, 2): "B"})
        s = from_board(Ruleset(push_own=False), cells)
        self.assertFalse(any((m.q, m.r, m.d) == (1, 0, E) for m in legal_moves(s)))
        # le spinte sull'avversario restano permesse
        s = from_board(Ruleset(push_own=False), board_of(A=[(0, 0)], B=[(1, 0)]))
        self.assertIn(Move(0, 0, E, True), legal_moves(s))

    def test_push_off(self):
        # B in (4,0) sul bordo est: A da (3,0) può buttarla fuori solo con push_off
        cells = board_of(A=[(3, 0)], B=[(4, 0), (-2, 2)])
        self.assertNotIn(Move(3, 0, E, True), legal_moves(from_board(Ruleset(), cells)))
        s = from_board(Ruleset(push_off=True), cells)
        self.assertIn(Move(3, 0, E, True), legal_moves(s))
        t = apply(s, Move(3, 0, E, True))
        self.assertEqual(t.board, {(4, 0): "A", (-2, 2): "B"})
        self.assertIsNone(winner(t))

    def test_push_off_never_own_piece(self):
        cells = board_of(A=[(3, 0), (4, 0)], B=[(-2, 2)])
        s = from_board(Ruleset(push_off=True), cells)
        self.assertNotIn(Move(3, 0, E, True), legal_moves(s))

    def test_push_off_home_row_wall(self):
        # il muro di casa non è più invulnerabile: B in (1,-3) butta fuori A da (1,-4)
        cells = board_of(A=[(0, -4), (1, -4), (2, -4)], B=[(1, -3)])
        s = from_board(Ruleset(push_off=True), cells, to_move="B")
        self.assertIn(Move(1, -3, NW, True), legal_moves(s))
        t = apply(s, Move(1, -3, NW, True))
        self.assertEqual(winner(t), "B")  # e arriva sul traguardo
        self.assertEqual(t.reason, "goal")

    def test_elimination_below_k(self):
        cells = board_of(A=[(3, 0), (0, 0)], B=[(4, 0)])
        t = apply(from_board(Ruleset(push_off=True), cells), Move(3, 0, E, True))
        self.assertEqual(winner(t), "A")
        self.assertEqual(t.reason, "elimination")
        # con K=2 basta restare con una pedina sola per perdere
        cells = board_of(A=[(3, 0), (0, 0)], B=[(4, 0), (-2, 2)])
        t = apply(from_board(Ruleset(push_off=True, K=2), cells), Move(3, 0, E, True))
        self.assertEqual(winner(t), "A")

    def test_pass_when_no_moves(self):
        # A nell'angolo est (3,0) con R=3: i 3 vicini sono B e dietro ognuno c'è un altro B
        s = from_board(Ruleset(R=3, N=6),
                       board_of(A=[(3, 0)], B=[(2, 0), (1, 0), (3, -1), (3, -2), (2, 1), (1, 2)]))
        self.assertEqual(legal_moves(s), [PASS])
        t = apply(s, PASS)
        self.assertEqual(t.board, s.board)
        self.assertEqual(t.to_move, "B")
        self.assertIsNone(winner(t))


class TestTwoMoves(unittest.TestCase):
    R2 = Ruleset(moves_per_turn=2)

    def test_opening_single_move(self):
        s = new_game(self.R2)
        self.assertEqual(s.moves_left, 1)
        t = apply(s, legal_moves(s)[0])
        self.assertEqual(t.to_move, "B")
        self.assertEqual(t.moves_left, 2)
        self.assertIsNone(t.locked)

    def test_two_moves_different_pieces(self):
        s = from_board(self.R2, board_of(A=[(0, 0), (-2, 1)], B=[(2, -2)]))
        t = apply(s, Move(0, 0, E, False))
        self.assertEqual((t.to_move, t.moves_left, t.locked), ("A", 1, (1, 0)))
        self.assertFalse(any((m.q, m.r) == (1, 0) for m in legal_moves(t)))
        u = apply(t, Move(-2, 1, E, False))
        self.assertEqual((u.to_move, u.moves_left, u.locked), ("B", 2, None))

    def test_train_push_allowed(self):
        # la seconda pedina può spingere quella già mossa (push_own)
        s = from_board(self.R2, board_of(A=[(0, 0), (-1, 0)], B=[(2, -2)]))
        t = apply(s, Move(0, 0, E, False))
        u = apply(t, Move(-1, 0, E, False))
        self.assertEqual(u.board, {(1, 0): "A", (0, 0): "A", (2, -2): "B"})
        t = apply(s, Move(0, 0, W, True))  # (0,0) spinge (-1,0) in (-2,0)
        # la pedina spinta non è bloccata e può a sua volta spingere quella bloccata
        self.assertIn(Move(-2, 0, E, True), legal_moves(t))

    def test_pass_second_move(self):
        # A ha una sola pedina: dopo la prima mossa non può più muovere, il turno passa
        s = from_board(self.R2, board_of(A=[(0, 0)], B=[(2, -2)]))
        t = apply(s, Move(0, 0, E, False))
        self.assertEqual(legal_moves(t), [PASS])
        u = apply(t, PASS)
        self.assertEqual((u.to_move, u.moves_left), ("B", 2))

    def test_win_on_first_half(self):
        s = from_board(self.R2, board_of(A=[(0, 3), (-2, 1)], B=[(2, -2)]))
        self.assertEqual(winner(apply(s, Move(0, 3, SE, False))), "A")

    def test_ko_two_moves(self):
        # B spinge due pedine di A; A non può rispingerle entrambe e ricreare la posizione
        rules = Ruleset(moves_per_turn=2, ko=True)
        cells = board_of(A=[(0, 0), (0, -2)], B=[(-1, 0), (-1, -2)])
        s = from_board(rules, cells, to_move="B")
        s = apply(s, Move(-1, 0, E, True))
        s = apply(s, Move(-1, -2, E, True))
        self.assertEqual(s.to_move, "A")
        s = apply(s, Move(1, 0, W, True))       # la prima rispinta è permessa
        self.assertEqual(s.board[(0, 0)], "A")
        self.assertNotIn(Move(1, -2, W, True), legal_moves(s))  # la seconda no
        self.assertIn(Move(1, -2, SW, False), legal_moves(s))
        # senza ko invece si può
        s = from_board(Ruleset(moves_per_turn=2), cells, to_move="B")
        for m in (Move(-1, 0, E, True), Move(-1, -2, E, True), Move(1, 0, W, True)):
            s = apply(s, m)
        self.assertIn(Move(1, -2, W, True), legal_moves(s))


class TestEnd(unittest.TestCase):
    def test_win_on_reaching_enemy_home(self):
        s = from_board(Ruleset(), board_of(A=[(0, 3)], B=[(0, -1)]))
        self.assertIsNone(winner(s))
        t = apply(s, Move(0, 3, SE, False))
        self.assertEqual(winner(t), "A")
        self.assertEqual(t.reason, "goal")
        self.assertEqual(legal_moves(t), [])
        with self.assertRaises(ValueError):
            apply(t, PASS)

    def test_win_for_b(self):
        s = from_board(Ruleset(), board_of(A=[(0, 1)], B=[(0, -3)]), to_move="B")
        self.assertEqual(winner(apply(s, Move(0, -3, NW, False))), "B")

    def test_win_by_pushing_own_piece(self):
        s = from_board(Ruleset(), board_of(A=[(0, 2), (0, 3)], B=[(0, -1)]))
        t = apply(s, Move(0, 2, SE, True))
        self.assertEqual(t.board[(0, 4)], "A")
        self.assertEqual(winner(t), "A")

    def test_pushing_opponent_onto_its_goal_makes_it_win(self):
        s = from_board(Ruleset(), board_of(A=[(1, -2)], B=[(1, -3)]))
        t = apply(s, Move(1, -2, NW, True))
        self.assertEqual(t.board[(1, -4)], "B")
        self.assertEqual(winner(t), "B")

    def test_k2_needs_two_pieces(self):
        rules = Ruleset(K=2)
        s = from_board(rules, board_of(A=[(0, 3), (-2, 3)], B=[(0, -1), (2, -2)]))
        s = apply(s, Move(0, 3, SE, False))
        self.assertIsNone(winner(s))
        s = apply(s, Move(2, -2, E, False))
        s = apply(s, Move(-2, 3, SE, False))
        self.assertEqual(winner(s), "A")

    def test_goal_wide(self):
        # (1,3) è la cella laterale accanto all'angolo (0,4) della casa di B
        cells = board_of(A=[(1, 2)], B=[(0, -1)])
        for wide, expected in ((True, "A"), (False, None)):
            t = apply(from_board(Ruleset(goal_wide=wide), cells), Move(1, 2, SE, False))
            self.assertEqual(t.board[(1, 3)], "A")
            self.assertEqual(winner(t), expected)

    def test_triple_repetition(self):
        s = from_board(Ruleset(), board_of(A=[(0, 0)], B=[(2, -2)]))
        cycle = [Move(0, 0, E, False), Move(2, -2, E, False),
                 Move(1, 0, W, False), Move(3, -2, W, False)]
        for i, m in enumerate(cycle * 2):
            self.assertIsNone(winner(s), f"finita troppo presto, semimossa {i}")
            s = apply(s, m)
        # la posizione iniziale è comparsa per la terza volta
        self.assertEqual(winner(s), "draw")
        self.assertEqual(s.reason, "repetition")
        self.assertEqual(s.ply, 8)

    def test_max_plies(self):
        s = from_board(Ruleset(max_plies=3), board_of(A=[(0, 0)], B=[(2, -2)]))
        for m in [Move(0, 0, E, False), Move(2, -2, E, False), Move(1, 0, SE, False)]:
            self.assertIsNone(winner(s))
            s = apply(s, m)
        self.assertEqual(winner(s), "draw")
        self.assertEqual(s.reason, "max_plies")

    def test_ko(self):
        cells = board_of(A=[(0, 0)], B=[(1, 0)])
        undo = Move(2, 0, W, True)  # B rispinge A e ricrea la posizione iniziale
        for ko in (False, True):
            s = apply(from_board(Ruleset(ko=ko), cells), Move(0, 0, E, True))
            moves = legal_moves(s)
            self.assertEqual(undo in moves, not ko)
            self.assertIn(Move(2, 0, E, False), moves)  # le altre mosse restano


class TestRandomGames(unittest.TestCase):
    def test_invariants(self):
        rng = random.Random(1)
        configs = [Ruleset(), Ruleset(R=3, N=3), Ruleset(R=5, N=8, K=2),
                   Ruleset(push_own=False, ko=True, goal_wide=True), Ruleset(push_off=True),
                   Ruleset(moves_per_turn=2, ko=True)]
        for rules in configs:
            geo = geometry(rules.R, rules.goal_wide)
            for _ in range(30):
                s = new_game(rules)
                while winner(s) is None:
                    moves = legal_moves(s)
                    self.assertTrue(moves)
                    if PASS in moves:
                        self.assertEqual(moves, [PASS])
                    s = apply(s, rng.choice(moves))
                    pieces = list(s.board.values())
                    if not rules.push_off:
                        self.assertEqual(pieces.count("A"), rules.N)
                        self.assertEqual(pieces.count("B"), rules.N)
                    self.assertTrue(set(s.board) <= geo.cells)
                self.assertLessEqual(s.ply, rules.max_plies)

    def test_render(self):
        for R in (3, 4, 5):
            text = render(new_game(Ruleset(R=R)), coords=False)
            tokens = " ".join(text.splitlines()[1:]).split()
            self.assertEqual(len(tokens), len(geometry(R).cells))
            self.assertEqual(tokens.count("A"), 5)


if __name__ == "__main__":
    unittest.main()
