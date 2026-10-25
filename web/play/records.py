"""Copia su database delle partite in memoria, e ritorno in memoria dopo un riavvio.

Funzioni sincrone (ORM di Django): dal codice asincrono si chiamano con
database_sync_to_async.
"""
import dataclasses
import logging

from django.contrib.auth.hashers import check_password, make_password
from django.db import IntegrityError
from django.utils import timezone

from hexpush.game import PASS, legal_moves
from hexpush.rules import Ruleset

from .matches import BOT, MATCHES, Match, clean_name, create_match
from .models import Game, Nickname, Player, Rating

log = logging.getLogger(__name__)
PASSWORD_MIN = 4
PASSWORD_MAX = 128


def get_player(pid):
    if not pid:
        return None
    return Player.objects.select_related("account").filter(pid=pid).first()


def set_player_name(pid, name, password=""):
    """Salva il nickname di un browser e lo aggiorna anche nelle partite in corso dove
    siede. ValueError se non va bene.

    Un nickname registrato (anche scritto con maiuscole diverse) vuole la sua password,
    a meno che questo browser non l'abbia già data. Un nickname libero con una password
    viene registrato; senza password resta anonimo e lo può usare chiunque."""
    name = clean_name(name)
    password = str(password or "")
    if len(password) > PASSWORD_MAX:
        raise ValueError(f"the password can be at most {PASSWORD_MAX} characters long")
    player = get_player(pid)
    account = Nickname.objects.filter(name__iexact=name).first()
    if account is not None:
        if player is None or player.account_id != account.pk:
            if not password:
                raise ValueError("this nickname is registered: enter its password, "
                                 "or choose another nickname")
            if not check_password(password, account.password):
                raise ValueError("wrong password for this nickname")
        name = account.name
    elif password:
        if len(password) < PASSWORD_MIN:
            raise ValueError(f"the password must be at least {PASSWORD_MIN} characters long")
        try:
            account = Nickname.objects.create(name=name, password=make_password(password))
        except IntegrityError:
            raise ValueError("this nickname has just been registered by someone else")
    player, _ = Player.objects.update_or_create(
        pid=pid, defaults={"name": name, "account": account})
    for match in MATCHES.values():
        seat = match.seat_of(pid)
        if seat is not None and match.result is None:
            match.names[seat] = name
            match.account_ids[seat] = player.account_id
    return player


def new_match(rules, player, color, level=None, rng=None):
    """Crea la partita in memoria e la registra."""
    match = create_match(rules, player.pid, player.name, color, level, rng, player.pk,
                         player.account_id)
    Game.objects.create(id=match.id, mode=match.mode, level=level or "",
                        rules=dataclasses.asdict(rules), created=match.started,
                        pieces=match.pieces,
                        bot_side=next((p for p, w in match.seats.items() if w == BOT), ""))
    save_match(match)
    return match


def save_match(match):
    """Riscrive la riga della partita: posti, nickname, mosse, annullamenti, esito."""
    ended = None
    if match.result is not None:
        ended = Game.objects.filter(pk=match.id).values_list("ended", flat=True).first() \
            or timezone.now()
    Game.objects.filter(pk=match.id).update(
        player_a_id=match.player_ids["A"],
        player_b_id=match.player_ids["B"],
        name_a=match.names["A"] or "",
        name_b=match.names["B"] or "",
        account_a_id=match.account_ids["A"],
        account_b_id=match.account_ids["B"],
        moves=[rec for _, _, rec in match.log],
        undone=match.undone,
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
        seats = (("A", game.player_a, game.name_a, game.account_a_id),
                 ("B", game.player_b, game.name_b, game.account_b_id))
        for color, player, name, account_id in seats:
            if game.bot_side == color:
                match.seats[color] = BOT
            elif player is not None:
                match.sit(color, player.pid, name or player.name, player.pk, account_id)
        for rec in game.moves:
            move = PASS
            if not rec.get("pass"):
                move = next(m for m in legal_moves(match.state) if m is not PASS
                            and [m.q, m.r, m.d] == rec["from"] + [rec["d"]])
            if match.state.to_move != rec["player"]:
                raise ValueError(f"mossa di {rec['player']} fuori turno")
            match.push(move, rec["t"], rec.get("bot"))
        match.undos = game.undos
        match.undone = list(game.undone)
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
