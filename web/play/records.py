"""Copia su database delle partite in memoria, e ritorno in memoria dopo un riavvio.

Funzioni sincrone (ORM di Django): dal codice asincrono si chiamano con
database_sync_to_async.
"""
import dataclasses
import logging

from django.utils import timezone

from hexpush.game import PASS, Move, legal_moves
from hexpush.rules import Ruleset

from .matches import BOT, MATCHES, Match, clean_name, create_match
from .models import Game, Player, Rating

log = logging.getLogger(__name__)


def get_player(pid):
    if not pid:
        return None
    return Player.objects.filter(pid=pid).first()


def set_player_name(pid, name):
    """Salva il nome di un browser (ValueError se non va bene) e lo aggiorna anche
    nelle partite in memoria dove siede."""
    name = clean_name(name)
    player, _ = Player.objects.update_or_create(pid=pid, defaults={"name": name})
    for match in MATCHES.values():
        seat = match.seat_of(pid)
        if seat is not None:
            match.names[seat] = name
    return player


def new_match(rules, player, color, level=None, rng=None):
    """Crea la partita in memoria e la registra."""
    match = create_match(rules, player.pid, player.name, color, level, rng, player.pk)
    Game.objects.create(id=match.id, mode=match.mode, level=level or "",
                        rules=dataclasses.asdict(rules), created=match.started,
                        bot_side=next((p for p, w in match.seats.items() if w == BOT), ""))
    save_match(match)
    return match


def save_match(match):
    """Riscrive la riga della partita: posti, mosse, annullamenti, esito."""
    ended = None
    if match.result is not None:
        ended = Game.objects.filter(pk=match.id).values_list("ended", flat=True).first() \
            or timezone.now()
    Game.objects.filter(pk=match.id).update(
        player_a_id=match.player_ids["A"],
        player_b_id=match.player_ids["B"],
        moves=[[p, None, None, None, t] if m is PASS else [p, m.q, m.r, m.d, t]
               for p, m, t in match.log],
        undos=match.undos,
        result=match.result or "",
        reason=match.reason or "",
        resigned=match.resigned or "",
        plies=match.state.ply,
        ended=ended,
    )


def save_rating(match, color, stars, comment=""):
    stars = match.check_rating(color, stars)
    Rating.objects.update_or_create(
        game_id=match.id, player_id=match.player_ids[color],
        defaults={"color": color, "stars": stars, "comment": str(comment or "")[:2000]})
    match.rated[color] = stars


def load_match(match_id):
    """Ricostruisce dal database una partita che non è più in memoria (riavvio del
    server), rigiocando le mosse registrate. None se non esiste o non si rigioca."""
    game = Game.objects.select_related("player_a", "player_b").filter(pk=match_id).first()
    if game is None:
        return None
    try:
        rules = Ruleset(**game.rules)
        match = Match(rules, game.level or None, match_id=game.id, started=game.created)
        for color, player in (("A", game.player_a), ("B", game.player_b)):
            if game.bot_side == color:
                match.seats[color] = BOT
            elif player is not None:
                match.sit(color, player.pid, player.name, player.pk)
        for p, q, r, d, t in game.moves:
            move = PASS if q is None else Move(q, r, d, False)
            if move is not PASS:
                move = next(m for m in legal_moves(match.state)
                            if m is not PASS and (m.q, m.r, m.d) == (q, r, d))
            if match.state.to_move != p:
                raise ValueError(f"mossa di {p} fuori turno")
            match.push(move, t)
        match.undos = game.undos
        match.resigned = game.resigned or None
        for rating in game.ratings.all():
            match.rated[rating.color] = rating.stars
    except Exception:
        log.exception("partita %s non ricostruibile", match_id)
        return None
    MATCHES[match.id] = match
    return match


def find_match(match_id):
    """Partita in memoria, oppure ricostruita dal database."""
    return MATCHES.get(match_id) or load_match(match_id)
