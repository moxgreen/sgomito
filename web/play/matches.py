"""Partite in corso, tenute in memoria nel processo del server.

Una Match avvolge il GameState del motore: le regole stanno tutte in hexpush, qui
ci sono solo i posti a sedere, l'annullamento della prima mossa, l'abbandono e il
registro delle mosse per disegnare l'ultimo turno.
"""
import random
import secrets

from hexpush.board import other
from hexpush.game import PASS, apply, legal_moves, new_game
from hexpush.mcts import MCTSDecisiveBot
from hexpush.serialize import move_to_dict, state_to_dict

# chiave -> (nome, simulazioni MCTS). Tempi con CPython: ~0.35 s ogni 1000 simulazioni.
LEVELS = {
    "facile": ("Facile", 200),
    "medio": ("Medio", 1000),
    "difficile": ("Difficile", 4000),
    "esperto": ("Esperto", 12000),
}
BOT = "computer"  # nei posti, al posto dell'identificativo di un giocatore umano

MATCHES = {}


class MoveError(Exception):
    """Mossa o comando rifiutato: il messaggio va mostrato a chi l'ha mandato."""


class Match:
    def __init__(self, rules, level=None, rng=None):
        self.id = new_id()
        self.rules = rules
        self.state = new_game(rules)
        self.log = []                     # (giocatore, mossa) in ordine, passi compresi
        self.turn_start = self.state      # stato all'inizio del turno in corso
        self.turn_start_log = 0           # len(log) all'inizio del turno in corso
        self.seats = {"A": None, "B": None}
        self.level = level
        self.bot = None
        if level is not None:
            self.bot = MCTSDecisiveBot(rng or random.Random(), LEVELS[level][1])
        self.resigned = None              # chi ha abbandonato
        self.thinking = False
        self.bot_task = None
        self.online = {}                  # pid -> connessioni aperte

    # --- posti ---------------------------------------------------------------------

    def seat_of(self, pid):
        if pid is None:
            return None
        for p, who in self.seats.items():
            if who == pid:
                return p
        return None

    def join(self, pid):
        """Colore di `pid`: il suo posto, o il primo libero; None se guarda soltanto."""
        seat = self.seat_of(pid)
        if seat is None and pid is not None:
            for p in ("A", "B"):
                if self.seats[p] is None:
                    self.seats[p], seat = pid, p
                    break
        return seat

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

    def apply_move(self, move):
        """Applica una mossa legale; poi passa da solo finché chi è di turno non ha mosse."""
        while True:
            mover = self.state.to_move
            self.state = apply(self.state, move)
            self.log.append((mover, move))
            if self.state.to_move != mover or self.state.result is not None:
                self.turn_start = self.state
                self.turn_start_log = len(self.log)
            if self.state.result is not None or legal_moves(self.state) != [PASS]:
                return
            move = PASS

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

    def resign(self, player):
        if player is None:
            raise MoveError("Stai guardando la partita: non puoi abbandonare.")
        if self.result is not None:
            raise MoveError("La partita è finita.")
        self.resigned = player

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
        return [move_to_dict(geo, m, p) for p, m in self.log[j:i + 1]]

    def players(self):
        out = {}
        for p, who in self.seats.items():
            if who == BOT:
                out[p] = f"computer ({LEVELS[self.level][0]})"
            else:
                out[p] = "libero" if who is None else "umano"
        return out

    def view(self, you):
        d = state_to_dict(self.state)
        d.update({
            "type": "state",
            "match": self.id,
            "you": you,
            "players": self.players(),
            "online": {p: who == BOT or self.online.get(who, 0) > 0
                       for p, who in self.seats.items()},
            "last_turn": self.last_turn(you),
            "can_undo": self.can_undo(you),
            "thinking": self.thinking,
            "result": self.result,
            "reason": self.reason,
        })
        if d["result"] is not None:
            d["legal"], d["ko_forbidden"] = [], []
        return d


def new_id():
    while True:
        mid = secrets.token_urlsafe(6).replace("-", "x").replace("_", "y")
        if mid not in MATCHES:
            return mid


def create_match(rules, creator, color, level=None, rng=None):
    """Nuova partita. `color` è 'A', 'B' o 'casuale'; con `level` l'altro posto è del
    computer, altrimenti resta libero per chi apre il link."""
    rng = rng or random.Random()
    if color not in ("A", "B"):
        color = rng.choice("AB")
    match = Match(rules, level, rng)
    match.seats[color] = creator
    if level is not None:
        match.seats[other(color)] = BOT
    MATCHES[match.id] = match
    return match


def get_match(match_id):
    return MATCHES.get(match_id)
