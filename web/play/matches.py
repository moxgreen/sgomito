"""Partite in corso, tenute in memoria nel processo del server.

Una Match avvolge il GameState del motore: le regole stanno tutte in hexpush, qui
ci sono solo i posti a sedere, l'annullamento della prima mossa, l'abbandono, il
registro delle mosse e i giudizi. La copia su database sta in records.py.

Il computer non calcola sul server: quando tocca a lui, lo stato mandato al giocatore
umano contiene la richiesta "bot" e il browser risponde con la mossa (play_bot), che
il server valida come quelle degli umani.
"""
import random
import secrets

from django.conf import settings
from django.utils import timezone

import dataclasses

from hexpush.board import DIR_NAMES, other
from hexpush.game import PASS, apply, legal_moves, new_game
from hexpush.mcts import TimePolicy
from hexpush.rules import Ruleset
from hexpush.serialize import move_targets, move_to_dict, search_state_to_dict, state_to_dict

# gestione del tempo del computer (mcts.TimePolicy, in JSON), uguale per tutti i livelli:
# None = sempre il budget pieno
TIME_POLICY = None


def mcts_level(name, n_sim):
    return name, f"{n_sim} simulations", {"n_sim": n_sim, "time_limit": None, "workers": 1,
                                         "policy": TIME_POLICY}


def champion_level():
    """Più alberi MCTS paralleli nel browser (un Web Worker per ogni due core) che si
    dividono un numero fisso di simulazioni: la forza non dipende dal dispositivo, solo
    l'attesa. Il limite di tempo è solo un tetto per i dispositivi lenti."""
    n_sim = settings.HEXPUSH_CHAMPION_SIMS
    return ("Champion", f"{n_sim} simulations on all cores",
            {"n_sim": n_sim, "time_limit": settings.HEXPUSH_CHAMPION_MAX_SECONDS,
             "workers": "auto", "policy": TIME_POLICY})


# Livelli a tempo: si sceglie quanto pensa il computer per mossa, in media sulla partita.
# La ricerca è guidata dalla policy appresa (mcts.js, opzione prior) se le regole sono
# quelle per cui è stata allenata, e usa tutti i core. Dove pensare di più lo decide la
# gestione del tempo: phase dà più tempo a 3-5 file dal traguardo, dove una ricerca più
# lunga cambia di più la mossa, e meno in apertura (distanza 7-8);
# early/extend fermano prima la ricerca quando la mossa è chiara e la allungano quando è
# instabile (results/thinktime_*, hexpush/thinktime.py). Il tempo risparmiato va in una
# banca (think_budget) e si spende nelle mosse dopo, così la media resta quella scelta.
THINK_POLICY = dataclasses.asdict(TimePolicy(early=0.5, extend=0.5, max_factor=1.6,
                                             phase=(0.7, 0.9, 1.3, 1.5, 1.3, 0.8, 0.5)))
THINK_RANGE = (0.5, 1.4)  # fattore del limite di tempo secondo la banca, al più
PRIOR_RULES = Ruleset(ko=True, no_pushback=True, moves_per_turn=2)  # regole del prior


def think_level(seconds):
    return (f"~{seconds:g} s/move", f"thinks about {seconds:g} s per move",
            {"n_sim": 10**7, "think": seconds, "workers": "auto", "policy": THINK_POLICY})


def think_budget(seconds, used):
    """Limite di tempo per la prossima mossa pensata dal computer, perché la media resti
    `seconds`: used sono i secondi delle mosse pensate finora (non quelle obbligate o
    vincenti, che non costano nulla). La banca è il tempo che resta per pareggiare la
    media contando anche questa mossa; si spende a poco a poco (THINK_RANGE)."""
    bank = seconds * (len(used) + 1) - sum(used)
    lo, hi = THINK_RANGE
    return round(seconds * min(max(bank / seconds, lo), hi), 3)


# chiave -> (nome, descrizione, parametri della ricerca nel browser: simulazioni in
# totale, limite in secondi, alberi paralleli). Vedi static/play/computer.js.
LEVELS = {
    "easy": mcts_level("Easy", 200),
    "medium": mcts_level("Medium", 1000),
    "hard": mcts_level("Hard", 4000),
    "expert": mcts_level("Expert", 12000),
    **{f"t{s:g}": think_level(s) for s in settings.HEXPUSH_THINK_SECONDS},
    # non più nel menu (sostituito dai livelli a tempo): resta per le partite salvate
    "champion": champion_level(),
}
MENU = [k for k in LEVELS if k != "champion"]
BOT = "computer"  # nei posti, al posto dell'identificativo di un giocatore umano
NAME_MAX = 30
# statistiche della ricerca che il browser manda con ogni mossa del computer
BOT_INFO = ("workers", "sims", "reused", "ms", "forced")

MATCHES = {}


class MoveError(Exception):
    """Mossa o comando rifiutato: il messaggio va mostrato a chi l'ha mandato."""


def clean_name(name):
    """Nome del giocatore ripulito; ValueError se vuoto o troppo lungo."""
    name = " ".join(str(name or "").split())
    if not name:
        raise ValueError("choose a nickname")
    if len(name) > NAME_MAX:
        raise ValueError(f"the nickname can be at most {NAME_MAX} characters long")
    return name


def piece_names(board):
    """Nome di ogni pedina, cella -> 'A1', 'A2', ...: per A in ordine di cella (prima r,
    poi q), per B nell'ordine riflesso, così B1 è l'immagine di A1 nel centro."""
    names = {}
    for p, order in (("A", lambda c: (c[1], c[0])), ("B", lambda c: (-c[1], -c[0]))):
        cells = sorted((c for c, owner in board.items() if owner == p), key=order)
        names.update({c: f"{p}{i}" for i, c in enumerate(cells, 1)})
    return names


def clean_bot_info(info):
    """Statistiche della ricerca mandate dal browser: solo le chiavi note, solo numeri."""
    if not isinstance(info, dict):
        return None
    out = {}
    for k in BOT_INFO:
        v = info.get(k)
        if isinstance(v, bool) or (isinstance(v, (int, float)) and abs(v) < 1e12):
            out[k] = round(v, 1) if isinstance(v, float) else v
    return out or None


class Match:
    def __init__(self, rules, level=None, match_id=None, started=None):
        self.id = match_id or new_id()
        self.rules = rules
        self.started = started or timezone.now()
        self.reset(new_game(rules))
        self.seats = {"A": None, "B": None}       # pid, BOT o None
        self.names = {"A": None, "B": None}
        self.player_ids = {"A": None, "B": None}  # chiavi di models.Player
        self.account_ids = {"A": None, "B": None}  # chiavi di models.Nickname (registrati)
        self.level = level
        self.resigned = None              # chi ha abbandonato
        self.undos = 0
        self.undone = []                  # mosse annullate: {"t", "moves": [record, ...]}
        self.rated = {}                   # colore -> stelle date
        self.online = {}                  # pid -> connessioni aperte

    def reset(self, state):
        """Riparte da `state`, senza mosse registrate; le pedine prendono nome da qui."""
        self.state = state
        self.log = []                     # (giocatore, mossa, record) in ordine, passi compresi
        self.turn_start = self.state      # stato all'inizio del turno in corso
        self.turn_start_log = 0           # len(log) all'inizio del turno in corso
        self.piece_at = piece_names(state.board)   # cella -> nome della pedina
        self.pieces = {name: list(c) for c, name in self.piece_at.items()}  # posizioni iniziali
        self.turn_start_pieces = dict(self.piece_at)

    @property
    def mode(self):
        return "friend" if self.level is None else "computer"

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

    def sit(self, color, pid, name, player_id=None, account_id=None):
        self.seats[color], self.names[color] = pid, name
        self.player_ids[color], self.account_ids[color] = player_id, account_id

    def join(self, pid, name=None, player_id=None, account_id=None):
        """Colore di `pid`: il suo posto, o il primo libero se ha un nome; None se guarda
        soltanto. Il secondo valore dice se ha appena preso posto."""
        seat = self.seat_of(pid)
        if seat is not None or pid is None or not name:
            return seat, False
        for p in ("A", "B"):
            if self.seats[p] is None:
                self.sit(p, pid, name, player_id, account_id)
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

    @property
    def thinking(self):
        """Il computer sta calcolando la sua mossa (nel browser di chi gioca)."""
        return self.bot_to_move()

    # --- comandi -------------------------------------------------------------------

    def _check_turn(self, player):
        if player is None:
            raise MoveError("You are watching this game: you can't move.")
        if self.result is not None:
            raise MoveError("The game is over.")
        if self.state.to_move != player:
            raise MoveError("It's not your turn.")

    def _find_move(self, q, r, d):
        for m in legal_moves(self.state):
            if m is not PASS and (m.q, m.r, m.d) == (q, r, d):
                return m
        raise MoveError("Invalid move.")

    def play(self, player, q, r, d):
        """Mossa di un giocatore umano, validata dal motore."""
        self._check_turn(player)
        move = self._find_move(q, r, d)
        self.apply_move(move)
        return move

    def play_bot(self, player, ply, q, r, d, info=None):
        """Mossa del computer calcolata nel browser di `player` per la semimossa `ply`,
        validata dal motore; `info` sono le statistiche della ricerca. False se arriva in
        ritardo (la partita è già andata avanti, per esempio l'ha mandata un'altra
        scheda): si ignora."""
        if self.level is None or player is None or self.seats[player] == BOT:
            raise MoveError("Only the player facing the computer can send its moves.")
        if ply != self.state.ply:
            return False
        if not self.bot_to_move():
            raise MoveError("It's not the computer's turn.")
        self.apply_move(self._find_move(q, r, d), clean_bot_info(info))
        return True

    def record(self, mover, move, t):
        """Riga del registro per `move`, giocata ora da `mover`: chi muove, quale pedina,
        da dove a dove, e quale pedina spinge e dove finisce ("off" se esce dal campo)."""
        rec = {"ply": self.state.ply, "player": mover, "t": t}
        if move is PASS:
            rec["pass"] = True
            return rec
        to, pushed_to = move_targets(self.state.geo, move)
        rec.update(piece=self.piece_at[(move.q, move.r)], **{"from": [move.q, move.r]},
                   d=move.d, dir=DIR_NAMES[move.d], to=to, push=move.push)
        if move.push:
            rec.update(pushed=self.piece_at[tuple(to)], pushed_to=pushed_to)
        return rec

    def push(self, move, t=None, bot=None):
        """Applica una mossa (già legale) e la registra."""
        mover = self.state.to_move
        rec = self.record(mover, move, self.elapsed() if t is None else t)
        if bot:
            rec["bot"] = bot
        self.state = apply(self.state, move)
        if move is not PASS:
            src, dst = (move.q, move.r), tuple(rec["to"])
            if move.push:
                pushed = self.piece_at.pop(dst)
                if rec["pushed_to"] != "off":
                    self.piece_at[tuple(rec["pushed_to"])] = pushed
            self.piece_at[dst] = self.piece_at.pop(src)
        self.log.append((mover, move, rec))
        if self.state.to_move != mover or self.state.result is not None:
            self.turn_start = self.state
            self.turn_start_log = len(self.log)
            self.turn_start_pieces = dict(self.piece_at)

    def apply_move(self, move, bot=None):
        """Applica una mossa legale; poi passa da solo finché chi è di turno non ha mosse."""
        self.push(move, bot=bot)
        while self.state.result is None and legal_moves(self.state) == [PASS]:
            self.push(PASS)

    def can_undo(self, player):
        return (player is not None and self.result is None
                and self.state.to_move == player and self.seats[player] != BOT
                and len(self.log) > self.turn_start_log)

    def undo(self, player):
        """Annulla la prima mossa del turno, finché la seconda non è stata fatta."""
        if not self.can_undo(player):
            raise MoveError("There is nothing to undo.")
        self.undone.append({"t": self.elapsed(),
                            "moves": [rec for _, _, rec in self.log[self.turn_start_log:]]})
        self.state = self.turn_start
        self.piece_at = dict(self.turn_start_pieces)
        del self.log[self.turn_start_log:]
        self.undos += 1

    def resign(self, player):
        if player is None:
            raise MoveError("You are watching this game: you can't resign.")
        if self.result is not None:
            raise MoveError("The game is over.")
        self.resigned = player

    def check_rating(self, player, stars):
        """Stelle (1-5) valide per un giudizio di `player`; MoveError altrimenti."""
        if player is None or self.seats[player] == BOT:
            raise MoveError("Only the players can rate the game.")
        if self.result is None:
            raise MoveError("You can rate the game once it is over.")
        if isinstance(stars, bool) or not isinstance(stars, int) or not 1 <= stars <= 5:
            raise MoveError("The rating goes from 1 to 5 stars.")
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
                out[p] = {"kind": "free", "name": None, "online": False}
            else:
                out[p] = {"kind": "human", "name": self.names[p],
                          "online": self.online.get(who, 0) > 0}
        return out

    def think_used(self):
        """Secondi di ricerca delle mosse pensate dal computer finora (registro "bot":
        si escludono quelle obbligate o vincenti)."""
        return [rec["bot"]["ms"] / 1000 for _, _, rec in self.log
                if rec.get("bot") and not rec["bot"].get("forced")
                and isinstance(rec["bot"].get("ms"), (int, float))]

    def bot_request(self):
        """Ciò che serve al browser per calcolare la mossa del computer: parametri del
        livello, pausa minima, semimossa a cui risponde e stato completo. Per i livelli a
        tempo il limite di questa mossa viene dalla banca del tempo (think_budget), e il
        prior si accende solo con le regole su cui è allenato."""
        req = dict(LEVELS[self.level][2], ply=self.state.ply,
                   delay=settings.HEXPUSH_BOT_DELAY, search=search_state_to_dict(self.state))
        seconds = req.pop("think", None)
        if seconds is not None:
            req["time_limit"] = think_budget(seconds, self.think_used())
            req["prior"] = self.rules == PRIOR_RULES
        return req

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
        if you is not None and self.bot_to_move() and self.seats[you] != BOT:
            d["bot"] = self.bot_request()
        return d


def new_id():
    while True:
        mid = secrets.token_urlsafe(6).replace("-", "x").replace("_", "y")
        if mid not in MATCHES:
            return mid


def create_match(rules, creator, name, color, level=None, rng=None, player_id=None,
                 account_id=None):
    """Nuova partita. `color` è 'A', 'B' o 'random'; con `level` l'altro posto è del
    computer, altrimenti resta libero per chi apre il link."""
    rng = rng or random.Random()
    if color not in ("A", "B"):
        color = rng.choice("AB")
    match = Match(rules, level)
    match.sit(color, creator, name, player_id, account_id)
    if level is not None:
        match.seats[other(color)] = BOT
    MATCHES[match.id] = match
    return match
