"""Registro del playtesting: chi gioca, le partite giocate, i giudizi sul divertimento.

La partita in corso vive in memoria (matches.Match); qui se ne tiene la copia che
sopravvive al riavvio del server e che serve per l'analisi.
"""
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


class Nickname(models.Model):
    """Nickname registrato con una password: solo chi la conosce può usarlo, da qualunque
    browser. Non si recupera. I nickname non registrati li può usare chiunque."""
    name = models.CharField(max_length=30)
    password = models.CharField(max_length=128)   # hash (django.contrib.auth.hashers)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(Lower("name"), name="nickname_unico")]

    def __str__(self):
        return self.name


class Player(models.Model):
    """Un browser (cookie hexpush_pid) con il nickname che il giocatore ha scelto;
    `account` se è un nickname registrato e questo browser ne ha dato la password."""
    pid = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=30)
    account = models.ForeignKey(Nickname, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="players")
    created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Game(models.Model):
    MODES = (("friend", "with a friend"), ("computer", "against the computer"))

    id = models.CharField(max_length=16, primary_key=True)
    created = models.DateTimeField(default=timezone.now)
    ended = models.DateTimeField(null=True, blank=True)
    mode = models.CharField(max_length=10, choices=MODES)
    level = models.CharField(max_length=12, blank=True)       # livello del computer
    rules = models.JSONField()                                # dataclasses.asdict(Ruleset)
    player_a = models.ForeignKey(Player, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="games_as_a")
    player_b = models.ForeignKey(Player, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="games_as_b")
    # nickname con cui hanno giocato (quello del Player può cambiare dopo) e, se
    # registrato, il suo Nickname
    name_a = models.CharField(max_length=30, blank=True)
    name_b = models.CharField(max_length=30, blank=True)
    account_a = models.ForeignKey(Nickname, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    account_b = models.ForeignKey(Nickname, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    bot_side = models.CharField(max_length=1, blank=True)     # 'A', 'B' o '' (due umani)
    # posizione iniziale di ogni pedina: {"A1": [q, r], ...} (matches.piece_names)
    pieces = models.JSONField(default=dict)
    # mosse giocate, passi compresi (Match.record): {"ply", "player", "t" (secondi
    # dall'inizio), "piece", "from", "d", "dir", "to", "push", e per le spinte "pushed",
    # "pushed_to" ([q, r] o "off")}; il passo ha "pass": true; le mosse del computer
    # hanno "bot" (statistiche della ricerca). Le mosse annullate non ci sono.
    moves = models.JSONField(default=list)
    # mosse annullate: [{"t": secondi, "moves": [come in moves]}, ...]
    undone = models.JSONField(default=list)
    undos = models.PositiveIntegerField(default=0)
    result = models.CharField(max_length=5, blank=True)       # 'A', 'B', 'draw'
    reason = models.CharField(max_length=12, blank=True)      # goal, resign, ...
    resigned = models.CharField(max_length=1, blank=True)
    plies = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return self.id


class Rating(models.Model):
    """Giudizio di un giocatore sul divertimento di una partita, da 1 a 5 stelle."""
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="ratings")
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="ratings")
    color = models.CharField(max_length=1)
    stars = models.PositiveSmallIntegerField()
    comment = models.TextField(blank=True)
    created = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["game", "player"], name="un_giudizio")]
