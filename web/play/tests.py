"""Test del frontend: viste, Match, registro e consumer WebSocket (manage.py test)."""
import asyncio
import csv
import io
import random

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.test import TestCase, TransactionTestCase, override_settings

from hexpush.game import from_board, legal_moves
from hexpush.parallel import ParallelMCTSBot
from hexpush.rules import Ruleset
from hexweb.asgi import application

from . import records
from .consumers import NOT_FOUND, PID_COOKIE
from .matches import BOT, MATCHES
from .models import Game, Player, Rating

RULES = Ruleset(ko=True, no_pushback=True, moves_per_turn=2)
E, W, SE, NW, NE, SW = range(6)


def person(pid, name):
    return Player.objects.update_or_create(pid=pid, defaults={"name": name})[0]


def make_match(color="A", level=None, rules=RULES, pid="p1", name="Ada"):
    return records.new_match(rules, person(pid, name), color, level)


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


def is_error(m):
    return m["type"] == "error"


async def join(match_id, pid):
    comm = communicator(match_id, pid)
    connected, _ = await comm.connect()
    assert connected
    state = await receive_until(comm, is_state())
    return comm, state


def move_msg(m):
    return {"type": "move", "q": m["q"], "r": m["r"], "d": m["d"]}


adb = database_sync_to_async


@override_settings(HEXPUSH_BOT_DELAY=0)
class ConsumerTests(TransactionTestCase):
    def setUp(self):
        MATCHES.clear()
        person("p2", "Bruno")
        person("p3", "Carla")

    async def two_players(self, **kwargs):
        match = await adb(make_match)(**kwargs)
        a, sa = await join(match.id, "p1")
        b, sb = await join(match.id, "p2")
        return match, a, sa, b, sb

    async def test_seats_names_and_spectator(self):
        match, a, sa, b, sb = await self.two_players()
        c, sc = await join(match.id, "p3")
        self.assertEqual((sa["you"], sb["you"], sc["you"]), ("A", "B", None))
        self.assertEqual(sb["players"]["A"], {"kind": "umano", "name": "Ada", "online": True})
        self.assertEqual(sb["players"]["B"]["name"], "Bruno")
        game = await adb(Game.objects.get)(pk=match.id)
        self.assertEqual((game.player_a_id, game.player_b_id),
                         (match.player_ids["A"], match.player_ids["B"]))
        # riconnessione: stesso cookie, stesso posto, stato completo
        await a.disconnect()
        a, sa = await join(match.id, "p1")
        self.assertEqual((sa["you"], sa["ply"]), ("A", 0))
        for comm in (a, b, c):
            await comm.disconnect()

    async def test_no_name_no_seat(self):
        match = await adb(make_match)()
        comm, st = await join(match.id, "senza-nome")
        self.assertIsNone(st["you"])
        self.assertEqual(st["players"]["B"]["kind"], "libero")
        await comm.disconnect()

    async def test_unknown_match_is_closed(self):
        comm = communicator("nonesiste", "p1")
        connected, _ = await comm.connect()
        self.assertTrue(connected)
        out = await comm.receive_output()
        self.assertEqual(out, {"type": "websocket.close", "code": NOT_FOUND})

    async def test_legal_move_is_broadcast_and_recorded(self):
        match, a, sa, b, _ = await self.two_players()
        move = sa["legal"][0]
        await a.send_json_to(move_msg(move))
        for comm, you in ((a, "A"), (b, "B")):
            st = await receive_until(comm, is_state(ply=1))
            self.assertEqual(st["you"], you)
            self.assertEqual((st["to_move"], st["moves_left"]), ("B", 2))
            self.assertEqual(st["last_move"]["to"], move["to"])
            self.assertEqual([m["player"] for m in st["last_turn"]], [] if you == "A" else ["A"])
        game = await adb(Game.objects.get)(pk=match.id)
        self.assertEqual(game.plies, 1)
        self.assertEqual(game.moves[0][:4], ["A", move["q"], move["r"], move["d"]])
        await a.disconnect()
        await b.disconnect()

    async def test_illegal_move_rejected(self):
        match, a, sa, b, _ = await self.two_players()
        # pedina di A in fila di casa: verso NW c'è il muro
        await a.send_json_to({"type": "move", "q": 0, "r": -4, "d": NW})
        err = await receive_until(a, is_error)
        self.assertEqual(err["message"], "Mossa non valida.")
        st = await receive_until(a, is_state())
        self.assertEqual(st["ply"], 0)
        await a.send_json_to({"type": "move", "q": "x"})
        self.assertEqual((await receive_until(a, is_error))["message"], "Mossa malformata.")
        await a.send_json_to({"type": "boh"})
        self.assertEqual((await receive_until(a, is_error))["message"], "Messaggio sconosciuto.")
        self.assertEqual(match.state.ply, 0)
        await a.disconnect()
        await b.disconnect()

    async def test_wrong_turn_rejected(self):
        match, a, sa, b, sb = await self.two_players()
        c, _ = await join(match.id, "p3")
        # B prova a muovere una sua pedina mentre tocca ad A
        cell = next((q, r) for q, r, p in sb["board"] if p == "B")
        await b.send_json_to({"type": "move", "q": cell[0], "r": cell[1], "d": NW})
        self.assertEqual((await receive_until(b, is_error))["message"], "Non è il tuo turno.")
        await c.send_json_to(move_msg(sa["legal"][0]))
        self.assertIn("Stai guardando", (await receive_until(c, is_error))["message"])
        self.assertEqual(match.state.ply, 0)
        for comm in (a, b, c):
            await comm.disconnect()

    async def test_undo_first_move_only(self):
        match, a, sa, b, _ = await self.two_players()
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
        await receive_until(a, is_error)
        await b.send_json_to({"type": "undo"})
        sb = await receive_until(b, is_state(ply=1))
        self.assertEqual((sb["to_move"], sb["moves_left"], sb["locked"]), ("B", 2, None))
        self.assertFalse(sb["can_undo"])
        game = await adb(Game.objects.get)(pk=match.id)
        self.assertEqual((game.undos, len(game.moves)), (1, 1))
        # dopo la seconda mossa il turno è chiuso: niente annullamento
        await b.send_json_to(move_msg(sb["legal"][0]))
        sb = await receive_until(b, is_state(ply=2))
        await b.send_json_to(move_msg(sb["legal"][0]))
        sb = await receive_until(b, is_state(ply=3))
        self.assertEqual(sb["to_move"], "A")
        await b.send_json_to({"type": "undo"})
        err = await receive_until(b, is_error)
        self.assertEqual(err["message"], "Non c'è niente da annullare.")
        self.assertEqual(match.state.ply, 3)
        await a.disconnect()
        await b.disconnect()

    async def test_game_over_by_goal(self):
        match, a, _, b, _ = await self.two_players()
        match.state = match.turn_start = from_board(
            RULES, {(0, 3): "A", (1, 3): "A", (0, -3): "B", (1, -3): "B"}, "A")
        await a.send_json_to({"type": "move", "q": 0, "r": 3, "d": SE})
        for comm in (a, b):
            st = await receive_until(comm, is_state(ply=1))
            self.assertEqual((st["result"], st["reason"]), ("A", "goal"))
            self.assertEqual(st["legal"], [])
            self.assertFalse(st["can_undo"])
            self.assertIsNone(st["rated"])
        await a.send_json_to({"type": "move", "q": 1, "r": 3, "d": SE})
        self.assertEqual((await receive_until(a, is_error))["message"], "La partita è finita.")
        game = await adb(Game.objects.get)(pk=match.id)
        self.assertEqual((game.result, game.reason), ("A", "goal"))
        self.assertIsNotNone(game.ended)
        await a.disconnect()
        await b.disconnect()

    async def test_resign_then_rate(self):
        match, a, _, b, _ = await self.two_players()
        c, _ = await join(match.id, "p3")
        # prima della fine niente giudizio
        await a.send_json_to({"type": "rate", "stars": 4})
        self.assertIn("finita", (await receive_until(a, is_error))["message"])
        await b.send_json_to({"type": "resign"})
        st = await receive_until(b, is_state(result="A"))
        self.assertEqual((st["reason"], st["resigned"]), ("resign", "B"))
        # chi ha abbandonato dà il suo giudizio, chi guarda no
        await b.send_json_to({"type": "rate", "stars": 2, "comment": "troppo lunga"})
        st = await receive_until(b, is_state(rated=2))
        await c.send_json_to({"type": "rate", "stars": 5})
        self.assertIn("Solo chi ha giocato", (await receive_until(c, is_error))["message"])
        for bad in (0, 6, "5", True, None):
            await a.send_json_to({"type": "rate", "stars": bad})
            self.assertIn("da 1 a 5", (await receive_until(a, is_error))["message"])
        await a.send_json_to({"type": "rate", "stars": 3})
        await receive_until(a, is_state(rated=3))
        await a.send_json_to({"type": "rate", "stars": 5, "comment": "bella"})  # cambia idea
        await receive_until(a, is_state(rated=5))
        ratings = await adb(lambda: sorted(
            (r.color, r.stars, r.comment) for r in Rating.objects.filter(game_id=match.id)))()
        self.assertEqual(ratings, [("A", 5, "bella"), ("B", 2, "troppo lunga")])
        game = await adb(Game.objects.get)(pk=match.id)
        self.assertEqual((game.result, game.reason, game.resigned), ("A", "resign", "B"))
        for comm in (a, b, c):
            await comm.disconnect()

    async def test_restored_after_restart(self):
        match, a, sa, b, _ = await self.two_players()
        await a.send_json_to(move_msg(sa["legal"][0]))
        sb = await receive_until(b, is_state(ply=1))
        await b.send_json_to(move_msg(sb["legal"][0]))
        sb = await receive_until(b, is_state(ply=2))
        await a.disconnect()
        await b.disconnect()
        MATCHES.clear()                                   # come un riavvio del server
        b, sb2 = await join(match.id, "p2")
        self.assertEqual((sb2["you"], sb2["ply"], sb2["locked"]), ("B", 2, sb["locked"]))
        self.assertEqual(sb2["board"], sb["board"])
        self.assertTrue(sb2["can_undo"])                  # la prima mossa di B è ancora sua
        self.assertEqual(sb2["players"]["A"]["name"], "Ada")
        await b.disconnect()

    async def test_bot_replies_with_two_moves(self):
        match = await adb(make_match)("A", "facile")
        self.assertEqual(match.seats["B"], BOT)
        a, sa = await join(match.id, "p1")
        self.assertEqual(sa["players"]["B"]["name"], "computer (Facile)")
        await a.send_json_to(move_msg(sa["legal"][0]))
        await receive_until(a, is_state(ply=1, thinking=True))
        st = await receive_until(a, is_state(ply=3))
        self.assertEqual((st["to_move"], st["thinking"]), ("A", False))
        self.assertEqual([m["player"] for m in st["last_turn"]], ["B", "B"])
        game = await adb(Game.objects.get)(pk=match.id)
        self.assertEqual((game.plies, game.bot_side, game.mode), (3, "B", "computer"))
        await a.send_json_to({"type": "resign"})
        await receive_until(a, is_state(result="B"))
        await a.send_json_to({"type": "rate", "stars": 4})
        await receive_until(a, is_state(rated=4))
        await a.disconnect()

    async def test_bot_opens_when_it_is_first(self):
        match = await adb(make_match)("B", "facile")
        b, _ = await join(match.id, "p1")
        st = await receive_until(b, is_state(ply=1))
        self.assertEqual((st["to_move"], st["thinking"], st["you"]), ("B", False, "B"))
        self.assertTrue(st["legal"])
        await b.disconnect()

    async def test_full_game_between_two_browsers(self):
        match, a, sa, b, _ = await self.two_players()
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
        game = await adb(Game.objects.get)(pk=match.id)
        self.assertEqual((game.result, game.plies, len(game.moves)),
                         (match.result, final["ply"], final["ply"]))
        await a.disconnect()
        await b.disconnect()


class ViewTests(TestCase):
    def setUp(self):
        MATCHES.clear()

    def pid(self):
        self.client.get("/")
        return self.client.cookies[PID_COOKIE].value

    def test_home_sets_player_cookie(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(PID_COOKIE, resp.cookies)
        self.assertContains(resp, "Gioca con un amico")
        self.assertContains(resp, "Esperto")
        self.assertContains(resp, 'name="name"')

    def test_name_is_required(self):
        resp = self.client.post("/nuova/", {"mode": "amico", "name": "   "})
        self.assertEqual(resp.status_code, 400)
        self.assertContains(resp, "scrivi il tuo nome", status_code=400)
        resp = self.client.post("/nuova/", {"mode": "amico", "name": "x" * 31})
        self.assertEqual(resp.status_code, 400)

    def test_create_friend_match(self):
        pid = self.pid()
        resp = self.client.post("/nuova/", {"mode": "amico", "color": "B", "R": 4, "N": 5,
                                            "K": 1, "name": "  Ada  Lovelace "})
        self.assertEqual(resp.status_code, 302)
        match_id = resp.url.rstrip("/").split("/")[-1]
        match = MATCHES[match_id]
        self.assertEqual(match.seats, {"A": None, "B": pid})
        self.assertEqual(match.names["B"], "Ada Lovelace")
        self.assertEqual(match.rules, RULES)
        self.assertEqual(Player.objects.get(pid=pid).name, "Ada Lovelace")
        game = Game.objects.get(pk=match_id)
        self.assertEqual((game.mode, game.player_b.name, game.player_a), ("amico", "Ada Lovelace", None))
        page = self.client.get(resp.url)
        self.assertContains(page, f'data-match="{match_id}"')
        self.assertContains(page, "Manda questo link")
        # il nome resta: la home lo propone già scritto
        self.assertContains(self.client.get("/"), 'value="Ada Lovelace"')

    def test_invited_player_must_give_a_name(self):
        match = make_match()
        url = f"/partita/{match.id}/"
        page = self.client.get(url)
        self.assertContains(page, "Come ti chiami?")
        self.assertNotContains(page, "data-match")
        resp = self.client.post(url, {"name": ""})
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post(url, {"name": "Bruno"})
        self.assertRedirects(resp, url, fetch_redirect_response=False)
        self.assertContains(self.client.get(url), f'data-match="{match.id}"')

    def test_create_computer_match_with_variant(self):
        self.pid()
        resp = self.client.post("/nuova/", {"mode": "computer", "color": "A", "level": "esperto",
                                            "R": 5, "N": 6, "K": 2, "goal_wide": "on",
                                            "name": "Ada"})
        self.assertEqual(resp.status_code, 302)
        match = MATCHES[resp.url.rstrip("/").split("/")[-1]]
        self.assertEqual(match.seats["B"], BOT)
        self.assertEqual(match.bot.n_sim, 12000)
        self.assertEqual((match.rules.R, match.rules.N, match.rules.K, match.rules.goal_wide),
                         (5, 6, 2, True))
        self.assertEqual(Game.objects.get(pk=match.id).bot_side, "B")

    def test_invalid_rules(self):
        resp = self.client.post("/nuova/", {"mode": "amico", "R": 4, "N": 3, "K": 4,
                                            "name": "Ada"})
        self.assertEqual(resp.status_code, 400)
        self.assertContains(resp, "Impossibile creare la partita", status_code=400)

    def test_unknown_match_page(self):
        self.assertEqual(self.client.get("/partita/nonesiste/").status_code, 404)

    def test_create_champion_match(self):
        self.pid()
        resp = self.client.post("/nuova/", {"mode": "computer", "color": "A", "level": "campione",
                                            "name": "Ada"})
        match = MATCHES[resp.url.rstrip("/").split("/")[-1]]
        self.assertIsInstance(match.bot, ParallelMCTSBot)
        self.assertEqual(match.bot.time_limit, settings.HEXPUSH_CHAMPION_SECONDS)
        self.assertContains(self.client.get("/"), "Campione (")

    def test_unknown_level(self):
        resp = self.client.post("/nuova/", {"mode": "computer", "level": "dio", "name": "Ada"})
        self.assertEqual(resp.status_code, 400)

    def test_registry(self):
        match = make_match()
        bruno = person("p2", "Bruno")
        match.join("p2", bruno.name, bruno.pk)
        match.apply_move(legal_moves(match.state)[0])
        match.resign("B")
        records.save_match(match)
        records.save_rating(match, "A", 5, "divertente")
        records.save_rating(match, "B", 3, "")
        other = make_match(level="medio", pid="p3", name="Carla")
        records.save_match(other)

        page = self.client.get("/registro/")
        self.assertContains(page, "Registro del playtest")
        self.assertContains(page, "2 partite, 1 finita,\n    2 giudizi")
        self.assertContains(page, "4,00")
        self.assertContains(page, "divertente")
        self.assertContains(page, "computer (Medio)")

        rows = list(csv.DictReader(io.StringIO(
            self.client.get("/registro/partite.csv").content.decode())))
        row = next(r for r in rows if r["id"] == match.id)
        self.assertEqual((row["giocatore_A"], row["giocatore_B"], row["esito"], row["motivo"],
                          row["stelle_A"], row["stelle_B"], row["semimosse"]),
                         ("Ada", "Bruno", "A", "resign", "5", "3", "1"))
        ratings = list(csv.DictReader(io.StringIO(
            self.client.get("/registro/giudizi.csv").content.decode())))
        self.assertEqual(sorted((r["giocatore"], r["stelle"]) for r in ratings),
                         [("Ada", "5"), ("Bruno", "3")])
        data = self.client.get(f"/registro/{match.id}.json").json()
        self.assertEqual(data["players"], {"A": "Ada", "B": "Bruno"})
        self.assertEqual(len(data["moves"]), 1)
