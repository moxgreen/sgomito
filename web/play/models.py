"""Registro del playtesting: chi gioca, le partite giocate, i giudizi sul divertimento.

La partita in corso vive in memoria (matches.Match); qui se ne tiene la copia che
sopravvive al riavvio del server e che serve per l'analisi.
"""
from django.db import models
from django.utils import timezone


class Player(models.Model):
    """Un browser (cookie hexpush_pid) con il nome che il giocatore ha scelto."""
    pid = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=30)
    created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Game(models.Model):
    MODES = (("amico", "con un amico"), ("computer", "contro il computer"))

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
    bot_side = models.CharField(max_length=1, blank=True)     # 'A', 'B' o '' (due umani)
    # mosse giocate, passi compresi: [giocatore, q, r, d, secondi dall'inizio]
    # (q, r, d = None per il passo). Le mosse annullate non ci sono.
    moves = models.JSONField(default=list)
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
