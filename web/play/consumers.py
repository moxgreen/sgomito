"""WebSocket di una partita: riceve move/undo/resign/rate, manda a tutti lo stato completo.
Ogni cambiamento della partita viene anche salvato nel registro (records.py)."""
import asyncio
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.layers import get_channel_layer
from django.conf import settings

from . import records
from .matches import MoveError

log = logging.getLogger(__name__)

PID_COOKIE = "hexpush_pid"
NOT_FOUND = 4404  # codice di chiusura: partita inesistente

find_match = database_sync_to_async(records.find_match)
get_player = database_sync_to_async(records.get_player)
save_match = database_sync_to_async(records.save_match)
save_rating = database_sync_to_async(records.save_rating)


def group_name(match):
    return f"partita_{match.id}"


async def broadcast(match):
    """Ogni consumer della partita rimanda lo stato visto dal proprio giocatore."""
    await get_channel_layer().group_send(group_name(match), {"type": "match.update"})


def start_bot(match):
    if match.bot_task is None and match.bot_to_move():
        match.bot_task = asyncio.create_task(run_bot(match))


async def run_bot(match):
    """Mosse del computer, una alla volta. Il calcolo gira in un thread: il ciclo degli
    eventi resta libero e intanto lo stato dice thinking=True."""
    loop = asyncio.get_running_loop()
    match.thinking = True
    try:
        await broadcast(match)
        while True:
            state = match.state
            t0 = loop.time()
            move = await asyncio.to_thread(match.bot.choose, state)
            pause = settings.HEXPUSH_BOT_DELAY - (loop.time() - t0)
            if pause > 0:
                await asyncio.sleep(pause)
            if match.state is not state or match.result is not None:
                break  # nel frattempo la partita è cambiata (abbandono)
            match.apply_move(move)
            await save_match(match)
            if not match.bot_to_move():
                break
            await broadcast(match)  # prima mossa del turno, la seconda è in arrivo
    except Exception:
        log.exception("errore del computer nella partita %s", match.id)
    finally:
        match.thinking = False
        match.bot_task = None
    await broadcast(match)


class MatchConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.match = await find_match(self.scope["url_route"]["kwargs"]["match_id"])
        if self.match is None:
            await self.accept()
            await self.close(code=NOT_FOUND)
            return
        self.pid = self.scope.get("cookies", {}).get(PID_COOKIE) or None
        # siede solo chi ha già dato il proprio nome (pagina della partita)
        person = await get_player(self.pid)
        self.player, seated = self.match.join(
            self.pid, person and person.name, person and person.pk)
        if seated:
            await save_match(self.match)
        self.group = group_name(self.match)
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        if self.pid is not None:
            self.match.online[self.pid] = self.match.online.get(self.pid, 0) + 1
        await broadcast(self.match)
        start_bot(self.match)

    async def disconnect(self, code):
        if getattr(self, "match", None) is None:
            return
        await self.channel_layer.group_discard(self.group, self.channel_name)
        if self.pid is not None:
            self.match.online[self.pid] -= 1
        await broadcast(self.match)

    async def receive_json(self, content, **kwargs):
        match, kind = self.match, content.get("type") if isinstance(content, dict) else None
        if match.thinking and kind in ("move", "undo"):
            await self.send_error("Il computer sta pensando.")
            return
        try:
            if kind == "move":
                try:
                    q, r, d = (int(content[k]) for k in ("q", "r", "d"))
                except (KeyError, TypeError, ValueError):
                    raise MoveError("Mossa malformata.")
                match.play(self.player, q, r, d)
            elif kind == "undo":
                match.undo(self.player)
            elif kind == "resign":
                match.resign(self.player)
            elif kind == "rate":
                await save_rating(match, self.player, content.get("stars"),
                                  content.get("comment", ""))
            else:
                raise MoveError("Messaggio sconosciuto.")
        except MoveError as e:
            await self.send_error(str(e))
            return
        if kind != "rate":
            await save_match(match)
        await broadcast(match)
        start_bot(match)

    async def send_error(self, message):
        await self.send_json({"type": "error", "message": message})
        # lo stato corrente, così il browser torna in pari
        await self.send_json(self.match.view(self.player))

    async def match_update(self, event):
        await self.send_json(self.match.view(self.player))
