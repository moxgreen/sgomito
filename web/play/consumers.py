"""WebSocket di una partita: riceve move/undo/resign/rate, manda a tutti lo stato completo.
Ogni cambiamento della partita viene anche salvato nel registro (records.py).

Contro il computer il server non calcola niente: lo stato del giocatore umano contiene
la richiesta "bot" e il suo browser risponde con bot_move (Match.play_bot)."""
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.layers import get_channel_layer

from . import records
from .matches import MoveError

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
            elif kind == "bot_move":
                try:
                    ply, q, r, d = (int(content[k]) for k in ("ply", "q", "r", "d"))
                except (KeyError, TypeError, ValueError):
                    raise MoveError("Mossa malformata.")
                if not match.play_bot(self.player, ply, q, r, d):
                    return  # in ritardo: la partita è già andata avanti
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

    async def send_error(self, message):
        await self.send_json({"type": "error", "message": message})
        # lo stato corrente, così il browser torna in pari
        await self.send_json(self.match.view(self.player))

    async def match_update(self, event):
        await self.send_json(self.match.view(self.player))
