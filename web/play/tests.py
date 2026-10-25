"""Test del frontend: viste, Match, registro e consumer WebSocket (manage.py test)."""
import asyncio
import csv
import io
import json
import random

from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.test import TestCase, TransactionTestCase, override_settings

from hexpush.game import Move, from_board, legal_moves
from hexpush.rules import Ruleset
from hexweb.asgi import application

from . import records
from .consumers import NOT_FOUND, PID_COOKIE
from .matches import BOT, LEVELS, MATCHES, THINK_POLICY, think_budget
from .models import Game, Nickname, Player, Rating

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
    return WebsocketCommunicator(application, f"/ws/game/{match_id}/", headers=headers)


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


def bot_msg(st, m=None):
    """Risposta del browser alla richiesta "bot": la prima mossa legale del computer."""
    m = m or st["legal"][0]
    return {"type": "bot_move", "ply": st["bot"]["ply"], "q": m["q"], "r": m["r"], "d": m["d"]}


async def play_bot(comm, st, n):
    """Fa la parte del browser per n mosse del computer; restituisce l'ultimo stato."""
    for _ in range(n):
        ply = st["bot"]["ply"]
        await comm.send_json_to(bot_msg(st))
        st = await receive_until(comm, is_state(ply=ply + 1))
    return st


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
        self.assertEqual(sb["players"]["A"], {"kind": "human", "name": "Ada", "online": True})
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
        self.assertEqual(st["players"]["B"]["kind"], "free")
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
        rec = game.moves[0]
        self.assertEqual((rec["ply"], rec["player"], rec["from"], rec["d"], rec["to"]),
                         (0, "A", [move["q"], move["r"]], move["d"], move["to"]))
        self.assertEqual(rec["piece"], match.piece_at[tuple(move["to"])])
        self.assertEqual((game.name_a, game.name_b), ("Ada", "Bruno"))
        self.assertEqual(len(game.pieces), 10)
        await a.disconnect()
        await b.disconnect()

    async def test_illegal_move_rejected(self):
        match, a, sa, b, _ = await self.two_players()
        # pedina di A in fila di casa: verso NW c'è il muro
        await a.send_json_to({"type": "move", "q": 0, "r": -4, "d": NW})
        err = await receive_until(a, is_error)
        self.assertEqual(err["message"], "Invalid move.")
        st = await receive_until(a, is_state())
        self.assertEqual(st["ply"], 0)
        await a.send_json_to({"type": "move", "q": "x"})
        self.assertEqual((await receive_until(a, is_error))["message"], "Malformed move.")
        await a.send_json_to({"type": "boh"})
        self.assertEqual((await receive_until(a, is_error))["message"], "Unknown message.")
        self.assertEqual(match.state.ply, 0)
        await a.disconnect()
        await b.disconnect()

    async def test_wrong_turn_rejected(self):
        match, a, sa, b, sb = await self.two_players()
        c, _ = await join(match.id, "p3")
        # B prova a muovere una sua pedina mentre tocca ad A
        cell = next((q, r) for q, r, p in sb["board"] if p == "B")
        await b.send_json_to({"type": "move", "q": cell[0], "r": cell[1], "d": NW})
        self.assertEqual((await receive_until(b, is_error))["message"], "It's not your turn.")
        await c.send_json_to(move_msg(sa["legal"][0]))
        self.assertIn("You are watching", (await receive_until(c, is_error))["message"])
        # tra due umani non c'è nessun computer di cui mandare le mosse
        await a.send_json_to(dict(move_msg(sa["legal"][0]), type="bot_move", ply=0))
        self.assertIn("facing the computer", (await receive_until(a, is_error))["message"])
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
        self.assertEqual(len(game.undone), 1)
        undone = game.undone[0]["moves"]
        self.assertEqual([(m["ply"], m["player"]) for m in undone], [(1, "B")])
        # la pedina annullata torna dov'era, con il suo nome
        self.assertEqual(match.piece_at[tuple(undone[0]["from"])], undone[0]["piece"])
        # dopo la seconda mossa il turno è chiuso: niente annullamento
        await b.send_json_to(move_msg(sb["legal"][0]))
        sb = await receive_until(b, is_state(ply=2))
        await b.send_json_to(move_msg(sb["legal"][0]))
        sb = await receive_until(b, is_state(ply=3))
        self.assertEqual(sb["to_move"], "A")
        await b.send_json_to({"type": "undo"})
        err = await receive_until(b, is_error)
        self.assertEqual(err["message"], "There is nothing to undo.")
        self.assertEqual(match.state.ply, 3)
        await a.disconnect()
        await b.disconnect()

    async def test_game_over_by_goal(self):
        match, a, _, b, _ = await self.two_players()
        match.reset(from_board(
            RULES, {(0, 3): "A", (1, 3): "A", (0, -3): "B", (1, -3): "B"}, "A"))
        await a.send_json_to({"type": "move", "q": 0, "r": 3, "d": SE})
        for comm in (a, b):
            st = await receive_until(comm, is_state(ply=1))
            self.assertEqual((st["result"], st["reason"]), ("A", "goal"))
            self.assertEqual(st["legal"], [])
            self.assertFalse(st["can_undo"])
            self.assertIsNone(st["rated"])
        await a.send_json_to({"type": "move", "q": 1, "r": 3, "d": SE})
        self.assertEqual((await receive_until(a, is_error))["message"], "The game is over.")
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
        self.assertIn("once it is over", (await receive_until(a, is_error))["message"])
        await b.send_json_to({"type": "resign"})
        st = await receive_until(b, is_state(result="A"))
        self.assertEqual((st["reason"], st["resigned"]), ("resign", "B"))
        # chi ha abbandonato dà il suo giudizio, chi guarda no
        await b.send_json_to({"type": "rate", "stars": 2, "comment": "troppo lunga"})
        st = await receive_until(b, is_state(rated=2))
        await c.send_json_to({"type": "rate", "stars": 5})
        self.assertIn("Only the players", (await receive_until(c, is_error))["message"])
        for bad in (0, 6, "5", True, None):
            await a.send_json_to({"type": "rate", "stars": bad})
            self.assertIn("from 1 to 5", (await receive_until(a, is_error))["message"])
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
        self.assertEqual(MATCHES[match.id].piece_at, match.piece_at)  # stessi nomi
        await b.disconnect()

    async def test_bot_replies_with_two_moves(self):
        match = await adb(make_match)("A", "easy")
        self.assertEqual(match.seats["B"], BOT)
        a, sa = await join(match.id, "p1")
        self.assertEqual(sa["players"]["B"]["name"], "computer (Easy)")
        self.assertNotIn("bot", sa)
        await a.send_json_to(move_msg(sa["legal"][0]))
        st = await receive_until(a, is_state(ply=1, thinking=True))
        req = st["bot"]
        self.assertEqual((req["ply"], req["n_sim"], req["time_limit"], req["workers"]),
                         (1, 200, None, 1))
        self.assertEqual((req["search"]["to_move"], len(req["search"]["history"])), ("B", 2))
        st = await play_bot(a, st, 2)
        self.assertEqual((st["to_move"], st["thinking"]), ("A", False))
        self.assertNotIn("bot", st)
        self.assertEqual([m["player"] for m in st["last_turn"]], ["B", "B"])
        game = await adb(Game.objects.get)(pk=match.id)
        self.assertEqual((game.plies, game.bot_side, game.mode), (3, "B", "computer"))
        self.assertEqual((game.name_a, game.name_b), ("Ada", ""))
        await a.send_json_to({"type": "resign"})
        await receive_until(a, is_state(result="B"))
        await a.send_json_to({"type": "rate", "stars": 4})
        await receive_until(a, is_state(rated=4))
        await a.disconnect()

    async def test_bot_opens_when_it_is_first(self):
        match = await adb(make_match)("B", "easy")
        b, st = await join(match.id, "p1")
        self.assertEqual((st["you"], st["thinking"], st["bot"]["ply"]), ("B", True, 0))
        # chi chiude la pagina (o il server che riparte) ritrova la richiesta
        await b.disconnect()
        MATCHES.clear()
        b, st = await join(match.id, "p1")
        self.assertEqual(st["bot"]["ply"], 0)
        st = await play_bot(b, st, 1)
        self.assertEqual((st["to_move"], st["thinking"], st["you"]), ("B", False, "B"))
        self.assertTrue(st["legal"])
        await b.disconnect()

    async def test_bot_move_checks(self):
        match = await adb(make_match)("B", "easy")
        b, st = await join(match.id, "p1")
        c, sc = await join(match.id, "p3")
        self.assertNotIn("bot", sc)                       # chi guarda non calcola
        msg = bot_msg(st)
        await c.send_json_to(msg)
        self.assertIn("facing the computer", (await receive_until(c, is_error))["message"])
        await b.send_json_to(dict(msg, q=0, r=-4, d=NW))  # pedina del computer contro il muro
        self.assertEqual((await receive_until(b, is_error))["message"], "Invalid move.")
        await b.send_json_to(dict(msg, ply="x"))
        self.assertEqual((await receive_until(b, is_error))["message"], "Malformed move.")
        # statistiche della ricerca: si tengono solo le chiavi note con valori numerici
        await b.send_json_to(dict(msg, info={"workers": 4, "sims": 812, "reused": 90,
                                             "ms": 1234, "forced": False, "evil": "x",
                                             "reused_by": [1]}))
        st = await receive_until(b, is_state(ply=1))
        self.assertNotIn("bot", st)
        # la stessa risposta da un'altra scheda arriva in ritardo: ignorata senza errori
        await b.send_json_to(msg)
        await b.send_json_to(dict(msg, ply=1))            # ora tocca all'umano
        self.assertEqual((await receive_until(b, is_error))["message"],
                         "It's not the computer's turn.")
        self.assertEqual(match.state.ply, 1)
        game = await adb(Game.objects.get)(pk=match.id)
        rec = game.moves[0]
        self.assertEqual((rec["player"], rec["from"], rec["d"]),
                         ("A", [msg["q"], msg["r"]], msg["d"]))
        self.assertEqual(rec["bot"], {"workers": 4, "sims": 812, "reused": 90, "ms": 1234,
                                      "forced": False})
        for comm in (b, c):
            await comm.disconnect()

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
        self.assertContains(resp, "Play with a friend")
        self.assertContains(resp, "Expert")
        self.assertContains(resp, 'name="name"')

    def test_name_is_required(self):
        resp = self.client.post("/new/", {"mode": "friend", "name": "   "})
        self.assertEqual(resp.status_code, 400)
        self.assertContains(resp, "choose a nickname", status_code=400)
        resp = self.client.post("/new/", {"mode": "friend", "name": "x" * 31})
        self.assertEqual(resp.status_code, 400)

    def test_create_friend_match(self):
        pid = self.pid()
        resp = self.client.post("/new/", {"mode": "friend", "color": "B", "R": 4, "N": 5,
                                            "K": 1, "name": "  Ada  Lovelace "})
        self.assertEqual(resp.status_code, 302)
        match_id = resp.url.rstrip("/").split("/")[-1]
        match = MATCHES[match_id]
        self.assertEqual(match.seats, {"A": None, "B": pid})
        self.assertEqual(match.names["B"], "Ada Lovelace")
        self.assertEqual(match.rules, RULES)
        self.assertEqual(Player.objects.get(pid=pid).name, "Ada Lovelace")
        game = Game.objects.get(pk=match_id)
        self.assertEqual((game.mode, game.player_b.name, game.player_a), ("friend", "Ada Lovelace", None))
        page = self.client.get(resp.url)
        self.assertContains(page, f'data-match="{match_id}"')
        self.assertContains(page, "Send this link")
        # il nome resta: la home lo propone già scritto
        self.assertContains(self.client.get("/"), 'value="Ada Lovelace"')

    def test_invited_player_must_give_a_name(self):
        match = make_match()
        url = f"/game/{match.id}/"
        page = self.client.get(url)
        self.assertContains(page, "Choose a nickname")
        self.assertNotContains(page, "data-match")
        resp = self.client.post(url, {"name": ""})
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post(url, {"name": "Bruno"})
        self.assertRedirects(resp, url, fetch_redirect_response=False)
        self.assertContains(self.client.get(url), f'data-match="{match.id}"')

    def test_create_computer_match_with_variant(self):
        self.pid()
        resp = self.client.post("/new/", {"mode": "computer", "color": "A", "level": "expert",
                                            "R": 5, "N": 6, "K": 2, "goal_wide": "on",
                                            "name": "Ada"})
        self.assertEqual(resp.status_code, 302)
        match = MATCHES[resp.url.rstrip("/").split("/")[-1]]
        self.assertEqual(match.seats["B"], BOT)
        self.assertEqual(match.bot_request()["n_sim"], 12000)
        self.assertEqual((match.rules.R, match.rules.N, match.rules.K, match.rules.goal_wide),
                         (5, 6, 2, True))
        self.assertEqual(Game.objects.get(pk=match.id).bot_side, "B")

    def test_invalid_rules(self):
        resp = self.client.post("/new/", {"mode": "friend", "R": 4, "N": 3, "K": 4,
                                            "name": "Ada"})
        self.assertEqual(resp.status_code, 400)
        self.assertContains(resp, "Couldn't create the game", status_code=400)

    def test_unknown_match_page(self):
        self.assertEqual(self.client.get("/game/nonesiste/").status_code, 404)

    def test_create_champion_match(self):
        self.pid()
        resp = self.client.post("/new/", {"mode": "computer", "color": "A", "level": "champion",
                                          "name": "Ada"})
        match = MATCHES[resp.url.rstrip("/").split("/")[-1]]
        req = match.bot_request()
        self.assertEqual((req["n_sim"], req["workers"], req["time_limit"]),
                         (settings.HEXPUSH_CHAMPION_SIMS, "auto",
                          settings.HEXPUSH_CHAMPION_MAX_SECONDS))
        # non più nel menu, sostituito dai livelli a tempo; le partite salvate restano
        self.assertNotContains(self.client.get("/"), "Champion (")

    def test_create_think_match(self):
        self.pid()
        home = self.client.get("/")
        for s in settings.HEXPUSH_THINK_SECONDS:
            self.assertContains(home, f'value="t{s:g}"')
        self.assertContains(home, "Thinks about 5 s per move")
        resp = self.client.post("/new/", {"mode": "computer", "color": "B", "level": "t5",
                                          "name": "Ada"})
        match = MATCHES[resp.url.rstrip("/").split("/")[-1]]
        req = match.bot_request()
        self.assertEqual((req["workers"], req["time_limit"], req["prior"], req["policy"]),
                         ("auto", 5.0, True, THINK_POLICY))
        self.assertNotIn("think", req)
        self.assertEqual(match.players()["A"]["name"], "computer (~5 s/move)")

    def test_unknown_level(self):
        resp = self.client.post("/new/", {"mode": "computer", "level": "god", "name": "Ada"})
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
        other = make_match(level="medium", pid="p3", name="Carla")
        records.save_match(other)

        page = self.client.get("/registry/")
        self.assertContains(page, "Playtest registry")
        self.assertContains(page, "2 games, 1 finished,\n    2 ratings")
        self.assertContains(page, "4.00")
        self.assertContains(page, "divertente")
        self.assertContains(page, "computer (Medium)")

        rows = list(csv.DictReader(io.StringIO(
            self.client.get("/registry/games.csv").content.decode())))
        row = next(r for r in rows if r["id"] == match.id)
        self.assertEqual((row["player_A"], row["player_B"], row["result"], row["reason"],
                          row["stars_A"], row["stars_B"], row["plies"], row["registered_A"]),
                         ("Ada", "Bruno", "A", "resign", "5", "3", "1", "0"))
        self.assertEqual(json.loads(row["moves"])[0]["piece"], match.log[0][2]["piece"])
        self.assertEqual(len(json.loads(row["pieces"])), 10)
        ratings = list(csv.DictReader(io.StringIO(
            self.client.get("/registry/ratings.csv").content.decode())))
        self.assertEqual(sorted((r["player"], r["stars"]) for r in ratings),
                         [("Ada", "5"), ("Bruno", "3")])
        data = self.client.get(f"/registry/{match.id}.json").json()
        self.assertEqual(data["players"], {"A": "Ada", "B": "Bruno"})
        self.assertEqual(len(data["moves"]), 1)

    def test_notice_on_entry_pages(self):
        for url in ("/", f"/game/{make_match().id}/"):
            page = self.client.get(url)
            self.assertContains(page, "Games are recorded for research")
            self.assertContains(page, "Passwords cannot be recovered")
            self.assertContains(page, 'name="password"')

    def test_old_italian_urls_redirect(self):
        match = make_match()
        resp = self.client.get(f"/partita/{match.id}/")
        self.assertRedirects(resp, f"/game/{match.id}/", status_code=301,
                             fetch_redirect_response=False)
        self.assertRedirects(self.client.get("/registro/"), "/registry/", status_code=301,
                             fetch_redirect_response=False)


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ThinkTimeTests(TestCase):
    """Livelli a tempo: banca del tempo e prior solo con le regole su cui è allenato."""

    def test_budget(self):
        self.assertEqual(think_budget(5, []), 5.0)
        self.assertEqual(think_budget(5, [4.0]), 6.0)        # 1 s risparmiato
        self.assertEqual(think_budget(5, [1.0, 1.0]), 7.0)   # banca 13 s, al più 1.4 x 5
        self.assertEqual(think_budget(5, [9.0]), 2.5)        # in debito, almeno 0.5 x 5
        self.assertEqual(think_budget(2, [2.5, 1.5]), 2.0)   # media esatta

    def bot_moves(self, match, infos):
        """Fa giocare al computer (A) e all'umano (B) mosse legali; `infos` sono le
        statistiche mandate dal browser per ogni mossa del computer."""
        infos = list(infos)
        while infos:
            if match.bot_to_move():
                m = legal_moves(match.state)[0]
                match.play_bot("B", match.state.ply, m.q, m.r, m.d, infos.pop(0))
            else:
                m = legal_moves(match.state)[0]
                match.play("B", m.q, m.r, m.d)

    def test_bank_follows_the_recorded_times(self):
        match = make_match("B", "t2")
        self.assertEqual(match.bot_request()["time_limit"], 2.0)
        self.bot_moves(match, [{"ms": 500, "workers": 4},          # 1.5 s risparmiati
                               {"ms": 3, "forced": True}])         # obbligata: non conta
        self.assertEqual(match.think_used(), [0.5])
        self.assertEqual(match.bot_request()["time_limit"], 2.8)    # 3.5 s, al più 1.4 x 2
        self.bot_moves(match, [{"ms": 4000}])
        self.assertEqual(match.bot_request()["time_limit"], 1.5)    # 6 - 4.5 = 1.5

    def test_bank_survives_a_restart(self):
        match = make_match("B", "t2")
        self.bot_moves(match, [{"ms": 500}])
        records.save_match(match)
        MATCHES.clear()
        again = records.find_match(match.id)
        self.assertEqual(again.think_used(), [0.5])

    def test_prior_only_with_the_trained_rules(self):
        self.assertTrue(make_match("B", "t1").bot_request()["prior"])
        variant = Ruleset(R=4, N=4, ko=True, no_pushback=True, moves_per_turn=2)
        self.assertFalse(make_match("B", "t1", rules=variant, pid="p2").bot_request()["prior"])
        self.assertNotIn("prior", make_match("B", "expert", pid="p3").bot_request())

    def test_levels(self):
        self.assertEqual(LEVELS["t10"][2]["workers"], "auto")
        self.assertIn("champion", LEVELS)


class NicknameTests(TestCase):
    def setUp(self):
        MATCHES.clear()

    def test_anonymous_nicknames_are_shared(self):
        records.set_player_name("p1", "Ada")
        records.set_player_name("p2", "Ada")
        self.assertFalse(Nickname.objects.exists())
        self.assertIsNone(Player.objects.get(pid="p2").account)

    def test_register_and_protect(self):
        player = records.set_player_name("p1", "Ada", "segreto")
        self.assertEqual(player.account.name, "Ada")
        self.assertNotIn("segreto", player.account.password)          # solo l'hash
        # stesso browser: niente password
        self.assertEqual(records.set_player_name("p1", "Ada").account, player.account)
        # altro browser, anche con maiuscole diverse
        for pw, msg in (("", "is registered"), ("sbagliata", "wrong password")):
            with self.assertRaisesRegex(ValueError, msg):
                records.set_player_name("p2", "ada", pw)
        other = records.set_player_name("p2", "ada", "segreto")
        self.assertEqual((other.name, other.account), ("Ada", player.account))
        with self.assertRaisesRegex(ValueError, "at least"):
            records.set_player_name("p3", "Bruno", "abc")
        # cambiare nickname scollega il browser dal nickname registrato
        self.assertIsNone(records.set_player_name("p1", "Carla").account)
        with self.assertRaisesRegex(ValueError, "is registered"):
            records.set_player_name("p1", "Ada")

    def test_registered_nickname_in_game_record(self):
        self.client.get("/")
        resp = self.client.post("/new/", {"mode": "computer", "level": "easy", "color": "A",
                                          "name": "Ada", "password": "segreto"})
        match_id = resp.url.rstrip("/").split("/")[-1]
        game = Game.objects.get(pk=match_id)
        self.assertEqual((game.name_a, game.account_a.name, game.account_b), ("Ada", "Ada", None))
        self.assertContains(self.client.get("/"), "signed in as <strong>Ada</strong>")
        # un altro browser non entra con quel nickname
        self.client.cookies.clear()
        resp = self.client.post(f"/game/{match_id}/", {"name": "Ada"})
        self.assertContains(resp, "This nickname is registered", status_code=400)


class PieceTests(TestCase):
    def match(self, board, rules=RULES):
        match = records.Match(rules)
        match.reset(from_board(rules, board, "A", moves_left=1))
        return match

    def test_initial_names_are_mirrored(self):
        match = records.Match(RULES)
        for name, (q, r) in match.pieces.items():
            if name[0] == "A":
                self.assertEqual(match.pieces["B" + name[1:]], [-q, -r])
        self.assertEqual(sorted(match.pieces), [f"{p}{i}" for p in "AB" for i in range(1, 6)])

    def test_push_moves_both_names(self):
        match = self.match({(0, 0): "A", (1, 0): "B", (-3, -1): "A", (3, 1): "B"})
        names = dict(match.piece_at)
        match.apply_move(Move(0, 0, E, True))
        rec = match.log[0][2]
        self.assertEqual((rec["piece"], rec["pushed"], rec["to"], rec["pushed_to"]),
                         (names[(0, 0)], names[(1, 0)], [1, 0], [2, 0]))
        self.assertEqual((match.piece_at[(1, 0)], match.piece_at[(2, 0)]),
                         (names[(0, 0)], names[(1, 0)]))
        self.assertNotIn((0, 0), match.piece_at)

    def test_push_off_removes_the_name(self):
        rules = Ruleset(ko=True, no_pushback=True, moves_per_turn=2, push_off=True, K=1)
        match = self.match({(2, 0): "A", (3, 0): "B", (4, 0): "B", (-3, -1): "A"}, rules)
        pushed = match.piece_at[(4, 0)]
        match.apply_move(Move(3, 0, E, True)) if False else None
        # A spinge B da (3, 0) a (4, 0)? dietro c'è un'altra pedina: si usa una spinta sul bordo
        match = self.match({(3, 0): "A", (4, 0): "B", (0, 3): "B", (-3, -1): "A"}, rules)
        pushed = match.piece_at[(4, 0)]
        match.apply_move(Move(3, 0, E, True))
        rec = match.log[0][2]
        self.assertEqual((rec["pushed"], rec["pushed_to"]), (pushed, "off"))
        self.assertNotIn(pushed, match.piece_at.values())
