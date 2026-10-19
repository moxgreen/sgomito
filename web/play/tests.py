"""Test del frontend: viste, Match e consumer WebSocket (manage.py test)."""
import asyncio
import random

from channels.testing import WebsocketCommunicator
from django.test import SimpleTestCase, override_settings

from hexpush.game import Move, from_board
from hexpush.rules import Ruleset
from hexweb.asgi import application

from .consumers import NOT_FOUND, PID_COOKIE
from .matches import BOT, MATCHES, create_match

RULES = Ruleset(ko=True, moves_per_turn=2)
E, W, SE, NW, NE, SW = range(6)


def communicator(match_id, pid=None):
    headers = [(b"origin", b"http://localhost"), (b"host", b"localhost")]
    if pid is not None:
        headers.append((b"cookie", f"{PID_COOKIE}={pid}".encode()))
    return WebsocketCommunicator(application, f"/ws/partita/{match_id}/", headers=headers)


async def receive_until(comm, pred, timeout=10):
    """Primo messaggio che soddisfa pred (gli altri si scartano)."""
    async def loop():
        while True:
            msg = await comm.receive_json_from(timeout=timeout)
            if pred(msg):
                return msg
    return await asyncio.wait_for(loop(), timeout)


def is_state(**fields):
    return lambda m: m["type"] == "state" and all(m[k] == v for k, v in fields.items())


async def join(match, pid):
    comm = communicator(match.id, pid)
    connected, _ = await comm.connect()
    assert connected
    state = await receive_until(comm, is_state())
    return comm, state


def move_msg(m):
    return {"type": "move", "q": m["q"], "r": m["r"], "d": m["d"]}


@override_settings(HEXPUSH_BOT_DELAY=0)
class ConsumerTests(SimpleTestCase):
    async def test_seats_and_spectator(self):
        match = create_match(RULES, "p1", "A")
        a, sa = await join(match, "p1")
        b, sb = await join(match, "p2")
        c, sc = await join(match, "p3")
        self.assertEqual((sa["you"], sb["you"], sc["you"]), ("A", "B", None))
        self.assertEqual(sb["players"], {"A": "umano", "B": "umano"})
        # riconnessione: stesso cookie, stesso posto, stato completo
        await a.disconnect()
        a, sa = await join(match, "p1")
        self.assertEqual(sa["you"], "A")
        self.assertEqual(sa["ply"], 0)
        for comm in (a, b, c):
            await comm.disconnect()

    async def test_unknown_match_is_closed(self):
        comm = communicator("nonesiste", "p1")
        connected, _ = await comm.connect()
        self.assertTrue(connected)
        out = await comm.receive_output()
        self.assertEqual(out, {"type": "websocket.close", "code": NOT_FOUND})

    async def test_legal_move_is_broadcast(self):
        match = create_match(RULES, "p1", "A")
        a, sa = await join(match, "p1")
        b, _ = await join(match, "p2")
        move = sa["legal"][0]
        await a.send_json_to(move_msg(move))
        for comm, you in ((a, "A"), (b, "B")):
            st = await receive_until(comm, is_state(ply=1))
            self.assertEqual(st["you"], you)
            self.assertEqual(st["to_move"], "B")
            self.assertEqual(st["moves_left"], 2)
            self.assertEqual(st["last_move"]["to"], move["to"])
            self.assertEqual([m["player"] for m in st["last_turn"]], [] if you == "A" else ["A"])
        await a.disconnect()
        await b.disconnect()

    async def test_illegal_move_rejected(self):
        match = create_match(RULES, "p1", "A")
        a, sa = await join(match, "p1")
        # pedina di A in fila di casa: verso NW c'è il muro
        await a.send_json_to({"type": "move", "q": 0, "r": -4, "d": NW})
        err = await receive_until(a, lambda m: m["type"] == "error")
        self.assertEqual(err["message"], "Mossa non valida.")
        st = await receive_until(a, is_state())
        self.assertEqual(st["ply"], 0)
        await a.send_json_to({"type": "move", "q": "x"})
        err = await receive_until(a, lambda m: m["type"] == "error")
        self.assertEqual(err["message"], "Mossa malformata.")
        await a.send_json_to({"type": "boh"})
        err = await receive_until(a, lambda m: m["type"] == "error")
        self.assertEqual(err["message"], "Messaggio sconosciuto.")
        self.assertEqual(match.state.ply, 0)
        await a.disconnect()

    async def test_wrong_turn_rejected(self):
        match = create_match(RULES, "p1", "A")
        a, sa = await join(match, "p1")
        b, sb = await join(match, "p2")
        c, _ = await join(match, "p3")
        # B prova a muovere una sua pedina mentre tocca ad A
        cell = next((q, r) for q, r, p in sb["board"] if p == "B")
        await b.send_json_to({"type": "move", "q": cell[0], "r": cell[1], "d": NW})
        err = await receive_until(b, lambda m: m["type"] == "error")
        self.assertEqual(err["message"], "Non è il tuo turno.")
        await c.send_json_to(move_msg(sa["legal"][0]))
        err = await receive_until(c, lambda m: m["type"] == "error")
        self.assertIn("Stai guardando", err["message"])
        self.assertEqual(match.state.ply, 0)
        for comm in (a, b, c):
            await comm.disconnect()

    async def test_undo_first_move_only(self):
        match = create_match(RULES, "p1", "A")
        a, sa = await join(match, "p1")
        b, _ = await join(match, "p2")
        await a.send_json_to(move_msg(sa["legal"][0]))      # apertura: una mossa sola
        sa = await receive_until(a, is_state(ply=1))
        self.assertFalse(sa["can_undo"])                     # il turno di A è finito
        sb = await receive_until(b, is_state(ply=1))
        await b.send_json_to(move_msg(sb["legal"][0]))
        sb = await receive_until(b, is_state(ply=2))
        self.assertTrue(sb["can_undo"])
        self.assertIsNotNone(sb["locked"])
        sa = await receive_until(a, is_state(ply=2))
        self.assertFalse(sa["can_undo"])
        # A non può annullare la mossa di B
        await a.send_json_to({"type": "undo"})
        await receive_until(a, lambda m: m["type"] == "error")
        await b.send_json_to({"type": "undo"})
        sb = await receive_until(b, is_state(ply=1))
        self.assertEqual((sb["to_move"], sb["moves_left"], sb["locked"]), ("B", 2, None))
        self.assertFalse(sb["can_undo"])
        # dopo la seconda mossa il turno è chiuso: niente annullamento
        await b.send_json_to(move_msg(sb["legal"][0]))
        sb = await receive_until(b, is_state(ply=2))
        await b.send_json_to(move_msg(sb["legal"][0]))
        sb = await receive_until(b, is_state(ply=3))
        self.assertEqual(sb["to_move"], "A")
        await b.send_json_to({"type": "undo"})
        err = await receive_until(b, lambda m: m["type"] == "error")
        self.assertEqual(err["message"], "Non c'è niente da annullare.")
        self.assertEqual(match.state.ply, 3)
        await a.disconnect()
        await b.disconnect()

    async def test_game_over_by_goal(self):
        match = create_match(RULES, "p1", "A")
        match.state = match.turn_start = from_board(
            RULES, {(0, 3): "A", (1, 3): "A", (0, -3): "B", (1, -3): "B"}, "A")
        a, _ = await join(match, "p1")
        b, _ = await join(match, "p2")
        await a.send_json_to({"type": "move", "q": 0, "r": 3, "d": SE})
        for comm in (a, b):
            st = await receive_until(comm, is_state(ply=1))
            self.assertEqual((st["result"], st["reason"]), ("A", "goal"))
            self.assertEqual(st["legal"], [])
            self.assertFalse(st["can_undo"])
        await a.send_json_to({"type": "move", "q": 1, "r": 3, "d": SE})
        err = await receive_until(a, lambda m: m["type"] == "error")
        self.assertEqual(err["message"], "La partita è finita.")
        await a.disconnect()
        await b.disconnect()

    async def test_resign(self):
        match = create_match(RULES, "p1", "A")
        a, _ = await join(match, "p1")
        b, _ = await join(match, "p2")
        await b.send_json_to({"type": "resign"})
        st = await receive_until(a, is_state(result="A"))
        self.assertEqual(st["reason"], "resign")
        await a.disconnect()
        await b.disconnect()

    async def test_bot_replies_with_two_moves(self):
        match = create_match(RULES, "p1", "A", level="facile")
        self.assertEqual(match.seats["B"], BOT)
        a, sa = await join(match, "p1")
        self.assertEqual(sa["players"]["B"], "computer (Facile)")
        await a.send_json_to(move_msg(sa["legal"][0]))
        await receive_until(a, is_state(ply=1, thinking=True))
        st = await receive_until(a, is_state(ply=3))
        self.assertEqual((st["to_move"], st["thinking"]), ("A", False))
        self.assertEqual([m["player"] for m in st["last_turn"]], ["B", "B"])
        await a.disconnect()

    async def test_bot_opens_when_it_is_first(self):
        match = create_match(RULES, "p1", "B", level="facile")
        b, _ = await join(match, "p1")
        st = await receive_until(b, is_state(ply=1))
        self.assertEqual((st["to_move"], st["thinking"], st["you"]), ("B", False, "B"))
        self.assertTrue(st["legal"])
        await b.disconnect()

    async def test_full_game_between_two_browsers(self):
        match = create_match(RULES, "p1", "A")
        a, sa = await join(match, "p1")
        b, sb = await join(match, "p2")
        rng = random.Random(3)
        comms = {"A": a, "B": b}
        st = sa
        while st["result"] is None:
            mover = comms[st["to_move"]]
            ply = st["ply"]
            await mover.send_json_to(move_msg(rng.choice(st["legal"])))
            st = await receive_until(mover, lambda m, p=ply: m["type"] == "state" and m["ply"] > p)
        other = b if mover is a else a
        final = await receive_until(other, is_state(ply=st["ply"]))
        self.assertIsNotNone(final["result"])
        self.assertEqual(final["result"], match.result)
        self.assertGreater(final["ply"], 2)
        await a.disconnect()
        await b.disconnect()


class ViewTests(SimpleTestCase):
    def test_home_sets_player_cookie(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(PID_COOKIE, resp.cookies)
        self.assertContains(resp, "Gioca con un amico")
        self.assertContains(resp, "Esperto")

    def test_create_friend_match(self):
        self.client.get("/")
        pid = self.client.cookies[PID_COOKIE].value
        resp = self.client.post("/nuova/", {"mode": "amico", "color": "B", "R": 4, "N": 5, "K": 1})
        self.assertEqual(resp.status_code, 302)
        match_id = resp.url.rstrip("/").split("/")[-1]
        match = MATCHES[match_id]
        self.assertEqual(match.seats, {"A": None, "B": pid})
        self.assertEqual(match.rules, RULES)
        page = self.client.get(resp.url)
        self.assertContains(page, f'data-match="{match_id}"')
        self.assertContains(page, "Manda questo link")

    def test_create_computer_match_with_variant(self):
        resp = self.client.post("/nuova/", {"mode": "computer", "color": "A", "level": "esperto",
                                            "R": 5, "N": 6, "K": 2, "goal_wide": "on"})
        self.assertEqual(resp.status_code, 302)
        match = MATCHES[resp.url.rstrip("/").split("/")[-1]]
        self.assertEqual(match.seats["B"], BOT)
        self.assertEqual(match.bot.n_sim, 12000)
        self.assertEqual((match.rules.R, match.rules.N, match.rules.K, match.rules.goal_wide),
                         (5, 6, 2, True))

    def test_invalid_rules(self):
        resp = self.client.post("/nuova/", {"mode": "amico", "R": 4, "N": 3, "K": 4})
        self.assertEqual(resp.status_code, 400)
        self.assertContains(resp, "Impossibile creare la partita", status_code=400)

    def test_unknown_match_page(self):
        self.assertEqual(self.client.get("/partita/nonesiste/").status_code, 404)

    def test_unknown_level(self):
        resp = self.client.post("/nuova/", {"mode": "computer", "level": "dio"})
        self.assertEqual(resp.status_code, 400)
