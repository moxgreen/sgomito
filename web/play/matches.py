"""Partite in corso, tenute in memoria nel processo del server.

Una Match avvolge il GameState del motore: le regole stanno tutte in hexpush, qui
ci sono solo i posti a sedere, l'annullamento della prima mossa, l'abbandono, il
registro delle mosse e i giudizi. La copia su database sta in records.py.
"""
import random
import secrets

from django.conf import settings
from django.utils import timezone

from hexpush.board import other
from hexpush.game import PASS, apply, legal_moves, new_game
from hexpush.mcts import MCTSDecisiveBot
from hexpush.parallel import ParallelMCTSBot, default_workers
from hexpush.serialize import move_to_dict, state_to_dict


def mcts_level(name, n_sim):
    return name, f"{n_sim} simulazioni", lambda rng: MCTSDecisiveBot(rng, n_sim)


def champion_level():
    """Più alberi MCTS in processi pypy3 paralleli, a tempo: con 4 processi pypy3 sono
    ~60000 simulazioni al secondo, contro ~3000 di un solo albero in CPython."""
    seconds, workers = settings.HEXPUSH_CHAMPION_SECONDS, default_workers()
    return ("Campione", f"{seconds:g} s per mossa su {workers} processi",
            lambda rng: ParallelMCTSBot(rng, 10**7, workers, time_limit=seconds))


# chiave -> (nome, descrizione, costruttore del bot dato un rng).
# Tempi con CPython: ~0.35 s ogni 1000 simulazioni.
LEVELS = {
    "facile": mcts_level("Facile", 200),
    "medio": mcts_level("Medio", 1000),
    "difficile": mcts_level("Difficile", 4000),
    "esperto": mcts_level("Esperto", 12000),
    "campione": champion_level(),
}
BOT = "computer"  # nei posti, al posto dell'identificativo di un giocatore umano
NAME_MAX = 30

MATCHES = {}


class MoveError(Exception):
    """Mossa o comando rifiutato: il messaggio va mostrato a chi l'ha mandato."""


def clean_name(name):
    """Nome del giocatore ripulito; ValueError se vuoto o troppo lungo."""
    name = " ".join(str(name or "").split())
    if not name:
        raise ValueError("scrivi il tuo nome")
    if len(name) > NAME_MAX:
        raise ValueError(f"il nome può avere al massimo {NAME_MAX} caratteri")
    return name


class Match:
    def __init__(self, rules, level=None, rng=None, match_id=None, started=None):
        self.id = match_id or new_id()
        self.rules = rules
        self.started = started or timezone.now()
        self.state = new_game(rules)
        self.log = []                     # (giocatore, mossa, secondi) in ordine, passi compresi
        self.turn_start = self.state      # stato all'inizio del turno in corso
        self.turn_start_log = 0           # len(log) all'inizio del turno in corso
        self.seats = {"A": None, "B": None}       # pid, BOT o None
        self.names = {"A": None, "B": None}
        self.player_ids = {"A": None, "B": None}  # chiavi di models.Player
        self.level = level
        self.bot = None
        if level is not None:
            self.bot = LEVELS[level][2](rng or random.Random())
        self.resigned = None              # chi ha abbandonato
        self.undos = 0
        self.rated = {}                   # colore -> stelle date
        self.thinking = False
        self.bot_task = None
        self.online = {}                  # pid -> connessioni aperte

    @property
    def mode(self):
        return "amico" if self.level is None else "computer"

    def elapsed(self):
        return round((timezone.now() - self.started).total_seconds(), 1)

    # --- posti ---------------------------------------------------------------------

    def seat_of(self, pid):
        if pid is None:
            return None
        for p, who in self.seats.items():
            if who == pid:
                return p
        return None

    def sit(self, color, pid, name, player_id=None):
        self.seats[color], self.names[color], self.player_ids[color] = pid, name, player_id

    def join(self, pid, name=None, player_id=None):
        """Colore di `pid`: il suo posto, o il primo libero se ha un nome; None se guarda
        soltanto. Il secondo valore dice se ha appena preso posto."""
        seat = self.seat_of(pid)
        if seat is not None or pid is None or not name:
            return seat, False
        for p in ("A", "B"):
            if self.seats[p] is None:
                self.sit(p, pid, name, player_id)
                return p, True
        return None, False

    # --- esito ---------------------------------------------------------------------

    @property
    def result(self):
        return other(self.resigned) if self.resigned else self.state.result

    @property
    def reason(self):
        return "resign" if self.resigned else self.state.reason

    def bot_to_move(self):
        return self.result is None and self.seats[self.state.to_move] == BOT

    # --- comandi -------------------------------------------------------------------

    def _check_turn(self, player):
        if player is None:
            raise MoveError("Stai guardando la partita: non puoi muovere.")
        if self.result is not None:
            raise MoveError("La partita è finita.")
        if self.state.to_move != player:
            raise MoveError("Non è il tuo turno.")

    def play(self, player, q, r, d):
        """Mossa di un giocatore umano, validata dal motore."""
        self._check_turn(player)
        for m in legal_moves(self.state):
            if m is not PASS and (m.q, m.r, m.d) == (q, r, d):
                self.apply_move(m)
                return m
        raise MoveError("Mossa non valida.")

    def push(self, move, t=None):
        """Applica una mossa (già legale) e la registra."""
        mover = self.state.to_move
        self.state = apply(self.state, move)
        self.log.append((mover, move, self.elapsed() if t is None else t))
        if self.state.to_move != mover or self.state.result is not None:
            self.turn_start = self.state
            self.turn_start_log = len(self.log)

    def apply_move(self, move):
        """Applica una mossa legale; poi passa da solo finché chi è di turno non ha mosse."""
        self.push(move)
        while self.state.result is None and legal_moves(self.state) == [PASS]:
            self.push(PASS)

    def can_undo(self, player):
        return (player is not None and self.result is None
                and self.state.to_move == player and self.seats[player] != BOT
                and len(self.log) > self.turn_start_log)

    def undo(self, player):
        """Annulla la prima mossa del turno, finché la seconda non è stata fatta."""
        if not self.can_undo(player):
            raise MoveError("Non c'è niente da annullare.")
        self.state = self.turn_start
        del self.log[self.turn_start_log:]
        self.undos += 1

    def resign(self, player):
        if player is None:
            raise MoveError("Stai guardando la partita: non puoi abbandonare.")
        if self.result is not None:
            raise MoveError("La partita è finita.")
        self.resigned = player

    def check_rating(self, player, stars):
        """Stelle (1-5) valide per un giudizio di `player`; MoveError altrimenti."""
        if player is None or self.seats[player] == BOT:
            raise MoveError("Solo chi ha giocato può dare un giudizio.")
        if self.result is None:
            raise MoveError("Il giudizio si dà a partita finita.")
        if isinstance(stars, bool) or not isinstance(stars, int) or not 1 <= stars <= 5:
            raise MoveError("Il giudizio va da 1 a 5 stelle.")
        return stars

    # --- vista per il browser ---------------------------------------------------------

    def last_turn(self, you):
        """Mosse dell'ultimo turno dell'avversario di `you` (anche se in corso).
        Per chi guarda: l'ultimo turno di chi ha mosso per ultimo."""
        i = len(self.log) - 1
        while i >= 0 and you is not None and self.log[i][0] == you:
            i -= 1
        if i < 0:
            return []
        who = self.log[i][0]
        j = i
        while j > 0 and self.log[j - 1][0] == who:
            j -= 1
        geo = self.state.geo
        return [move_to_dict(geo, m, p) for p, m, _ in self.log[j:i + 1]]

    def players(self):
        out = {}
        for p, who in self.seats.items():
            if who == BOT:
                out[p] = {"kind": "computer", "name": f"computer ({LEVELS[self.level][0]})",
                          "online": True}
            elif who is None:
                out[p] = {"kind": "libero", "name": None, "online": False}
            else:
                out[p] = {"kind": "umano", "name": self.names[p],
                          "online": self.online.get(who, 0) > 0}
        return out

    def view(self, you):
        d = state_to_dict(self.state)
        d.update({
            "type": "state",
            "match": self.id,
            "you": you,
            "players": self.players(),
            "last_turn": self.last_turn(you),
            "can_undo": self.can_undo(you),
            "thinking": self.thinking,
            "result": self.result,
            "reason": self.reason,
            "resigned": self.resigned,
            "rated": self.rated.get(you) if you else None,
        })
        if d["result"] is not None:
            d["legal"], d["ko_forbidden"] = [], []
        return d


def new_id():
    while True:
        mid = secrets.token_urlsafe(6).replace("-", "x").replace("_", "y")
        if mid not in MATCHES:
            return mid


def create_match(rules, creator, name, color, level=None, rng=None, player_id=None):
    """Nuova partita. `color` è 'A', 'B' o 'casuale'; con `level` l'altro posto è del
    computer, altrimenti resta libero per chi apre il link."""
    rng = rng or random.Random()
    if color not in ("A", "B"):
        color = rng.choice("AB")
    match = Match(rules, level, rng)
    match.sit(color, creator, name, player_id)
    if level is not None:
        match.seats[other(color)] = BOT
    MATCHES[match.id] = match
    return match
