"""MCTS con UCT e playout casuali.

Tutto gira su FastBoard: celle indicizzate, campo come lista mutabile. I nodi
dell'albero contengono solo la mossa; a ogni simulazione si copia il campo della
radice e si rigiocano le mosse scendendo. Le ripetizioni nell'albero sono esatte:
ogni posizione ha un hash di Zobrist incrementale, e si contano le occorrenze nella
storia della partita più quelle lungo il percorso.

Approssimazione dichiarata: nei playout non si contano le ripetizioni (la partita
finisce solo per vittoria o per max_plies). Il ko invece è esatto ovunque: si
confronta l'hash della posizione di fine turno con quello d'inizio del turno
precedente. Basta controllare le spinte: una mossa semplice non può mai ricreare
quella posizione, perché nessuna mossa dell'avversario sposta le pedine di chi muove
se non spingendole. Anche no_pushback è esatto: si tengono le spinte inverse vietate.
"""
import math
import random
import time
from dataclasses import dataclass
from functools import lru_cache

from .board import OPPOSITE, geometry
from .game import Move, legal_moves

A, B = 1, 2
CODE = {"A": A, "B": B}
NAME = {A: "A", B: "B"}


class FastGeo:
    """win_dirs[p][i]: direzioni in cui una pedina di p in i può portare una pedina sul
    traguardo in una mossa (muovendosi, o spingendo una propria pedina); quasi sempre
    vuoto, così il controllo delle mosse decisive costa poco."""

    __slots__ = ("cells", "index", "nbr", "goal", "win_dirs", "n", "zobrist", "z_side",
                 "z_lock", "z_two")

    def __init__(self, R, goal_wide):
        geo = geometry(R, goal_wide)
        self.cells = sorted(geo.cells)
        self.n = len(self.cells)
        self.index = {c: i for i, c in enumerate(self.cells)}
        self.nbr = [[-1 if t is None else self.index[t] for t in geo.nbr[c]] for c in self.cells]
        self.goal = [None, [c in geo.goal["A"] for c in self.cells],
                     [c in geo.goal["B"] for c in self.cells]]
        self.win_dirs = [None]
        for p in (A, B):
            goal, dirs = self.goal[p], []
            for i in range(self.n):
                ds = []
                for d in range(6):
                    t = self.nbr[i][d]
                    if t < 0:
                        continue
                    t2 = self.nbr[t][d]
                    if goal[t] or (t2 >= 0 and goal[t2]):
                        ds.append(d)
                dirs.append(ds)
            self.win_dirs.append(dirs)
        zr = random.Random(f"zobrist-{R}")
        self.zobrist = [None] + [[zr.getrandbits(62) for _ in range(self.n)] for _ in (A, B)]
        self.z_side = zr.getrandbits(62)  # presente quando muove B
        self.z_lock = [zr.getrandbits(62) for _ in range(self.n)]  # pedina bloccata nel turno
        self.z_two = zr.getrandbits(62)  # presente quando restano 2 mosse nel turno

    def hash_key(self, key):
        """Hash di una chiave di game.position_key."""
        cells, to_move, locked, moves_left = key
        h = self.z_side if to_move == "B" else 0
        if locked is not None:
            h ^= self.z_lock[self.index[locked]]
        if moves_left == 2:
            h ^= self.z_two
        for cell, owner in cells:
            h ^= self.zobrist[CODE[owner]][self.index[cell]]
        return h


@lru_cache(maxsize=None)
def fast_geometry(R, goal_wide):
    return FastGeo(R, goal_wide)


class FastBoard:
    """Copia mutabile di uno stato per i playout."""

    __slots__ = ("geo", "rules", "board", "pieces", "pid", "goal_count", "to_move", "ply",
                 "moves_left", "locked", "hash", "turn_start", "ko_hash", "turn_pushes",
                 "banned")

    def __init__(self, state):
        g = fast_geometry(state.rules.R, state.rules.goal_wide)
        self.geo = g
        self.rules = state.rules
        self.board = [0] * g.n
        self.pieces = [None, [], []]
        self.pid = [0] * g.n
        self.goal_count = [0, 0, 0]
        for cell, owner in state.board.items():
            i, p = g.index[cell], CODE[owner]
            self.board[i] = p
            self.pid[i] = len(self.pieces[p])
            self.pieces[p].append(i)
            self.goal_count[p] += g.goal[p][i]
        self.to_move = CODE[state.to_move]
        self.ply = state.ply
        self.moves_left = state.moves_left
        self.locked = -1 if state.locked is None else g.index[state.locked]
        self.hash = g.hash_key(state.key)
        # ko: hash d'inizio del turno corrente e del precedente (quello vietato a fine turno)
        self.turn_start = g.hash_key(state.turn_keys[-1])
        self.ko_hash = None
        if state.rules.ko and len(state.turn_keys) >= 2:
            self.ko_hash = g.hash_key(state.turn_keys[-2])
        # no_pushback: spinte inverse come cella * 6 + direzione (vedi GameState)
        self.turn_pushes = tuple(g.index[(q, r)] * 6 + d for q, r, d in state.turn_pushes)
        self.banned = tuple(g.index[(q, r)] * 6 + d for q, r, d in state.banned)

    def copy(self):
        fb = FastBoard.__new__(FastBoard)
        fb.geo, fb.rules = self.geo, self.rules
        fb.board, fb.pid = self.board[:], self.pid[:]
        fb.pieces = [None, self.pieces[A][:], self.pieces[B][:]]
        fb.goal_count = self.goal_count[:]
        fb.to_move, fb.ply, fb.hash = self.to_move, self.ply, self.hash
        fb.moves_left, fb.locked = self.moves_left, self.locked
        fb.turn_start, fb.ko_hash = self.turn_start, self.ko_hash
        fb.turn_pushes, fb.banned = self.turn_pushes, self.banned
        return fb

    def is_legal(self, src, d):
        """src: cella con una pedina di chi muove. Restituisce 0 illegale, 1 semplice,
        2 spinta, 3 spinta fuori dal campo (push_off)."""
        if src == self.locked:
            return 0
        t = self.geo.nbr[src][d]
        if t < 0:
            return 0
        o = self.board[t]
        if o == 0:
            return 1
        if o == self.to_move and not self.rules.push_own:
            return 0
        t2 = self.geo.nbr[t][d]
        if t2 < 0:
            return 3 if self.rules.push_off and o != self.to_move else 0
        if self.board[t2]:
            return 0
        if self.banned and src * 6 + d in self.banned and o != self.to_move:
            return 0
        if self.ko_hash is not None and self.moves_left == 1:
            # hash della posizione d'inizio del turno avversario che questa mossa creerebbe
            g = self.geo
            zp, zo = g.zobrist[self.to_move], g.zobrist[o]
            h = self.hash ^ zp[src] ^ zp[t] ^ zo[t] ^ zo[t2] ^ g.z_side
            if self.locked >= 0:
                h ^= g.z_lock[self.locked]
            if self.rules.moves_per_turn == 2:
                h ^= g.z_two
            if h == self.ko_hash:
                return 0
        return 2

    def moves(self):
        """Lista di (cella, direzione, tipo) legali; usata dai test e come ripiego."""
        out = []
        for src in self.pieces[self.to_move]:
            for d in range(6):
                k = self.is_legal(src, d)
                if k:
                    out.append((src, d, k))
        return out

    def _move_piece(self, frm, to):
        p = self.board[frm]
        k = self.pid[frm]
        self.board[frm] = 0
        self.board[to] = p
        self.pid[to] = k
        self.pieces[p][k] = to
        g = self.geo.goal[p]
        self.goal_count[p] += g[to] - g[frm]
        z = self.geo.zobrist[p]
        self.hash ^= z[frm] ^ z[to]

    def _remove_piece(self, cell):
        p = self.board[cell]
        k = self.pid[cell]
        mine = self.pieces[p]
        last = mine.pop()
        if last != cell:
            mine[k] = last
            self.pid[last] = k
        self.board[cell] = 0
        self.goal_count[p] -= self.geo.goal[p][cell]
        self.hash ^= self.geo.zobrist[p][cell]

    def play(self, src, d, kind):
        """Applica una mossa legale; restituisce il vincitore (1/2) o 0."""
        me = self.to_move
        nbr = self.geo.nbr
        dst = nbr[src][d]
        if kind == 3:
            self._remove_piece(dst)
        elif kind == 2:
            if self.rules.no_pushback and self.board[dst] != me:
                self.turn_pushes += (nbr[dst][d] * 6 + OPPOSITE[d],)
            self._move_piece(dst, nbr[dst][d])
        self._move_piece(src, dst)
        self.ply += 1
        if self.moves_left > 1:
            # stesso turno: la pedina appena mossa resta ferma
            self.moves_left = 1
            self.locked = dst
            self.hash ^= self.geo.z_two ^ self.geo.z_lock[dst]
        else:
            self._end_turn()
        K = self.rules.K
        if self.goal_count[me] >= K:
            return me
        if self.goal_count[3 - me] >= K:
            return 3 - me
        if kind == 3 and len(self.pieces[3 - me]) < K:
            return me
        return 0

    def _end_turn(self):
        g = self.geo
        if self.locked >= 0:
            self.hash ^= g.z_lock[self.locked]
            self.locked = -1
        self.to_move = 3 - self.to_move
        self.hash ^= g.z_side
        self.moves_left = self.rules.moves_per_turn
        if self.moves_left == 2:
            self.hash ^= g.z_two
        if self.rules.ko:
            self.ko_hash = self.turn_start
        self.turn_start = self.hash
        self.banned, self.turn_pushes = self.turn_pushes, ()

    def pass_turn(self):
        """Il passo chiude sempre il turno."""
        self.ply += 1
        self._end_turn()

    def winning_move(self):
        """Una mossa che fa vincere subito chi muove, o None."""
        me = self.to_move
        win_dirs, goal, nbr = self.geo.win_dirs[me], self.geo.goal[me], self.geo.nbr
        need = self.rules.K - self.goal_count[me]
        for src in self.pieces[me]:
            for d in win_dirs[src]:
                kind = self.is_legal(src, d)
                if not kind:
                    continue
                dst = nbr[src][d]
                gain = goal[dst] - goal[src]
                if kind == 2 and self.board[dst] == me:
                    gain += goal[nbr[dst][d]] - goal[dst]
                if gain >= need:
                    return src, d, kind
        if self.rules.push_off and len(self.pieces[3 - me]) <= self.rules.K:
            # eliminare una pedina avversaria basta a vincere
            for src, d, kind in self.moves():
                if kind == 3:
                    return src, d, kind
        return None

    def random_playout(self, rng, limit=None, decisive=False):
        """Gioca a caso fino alla fine (o per `limit` semimosse).

        Con decisive=True chi muove sceglie una vittoria immediata quando c'è
        ("mosse decisive": conoscenza delle regole, non euristica strategica).
        Restituisce il valore per A in [0, 1]: 1 vittoria, 0 sconfitta, 0.5 patta per
        max_plies; se troncato, 0.5 + differenza di avanzamento normalizzata.
        """
        max_plies = self.rules.max_plies
        stop = max_plies if limit is None else min(max_plies, self.ply + limit)
        randrange = rng.randrange
        while self.ply < stop:
            if decisive:
                win = self.winning_move()
                if win is not None:
                    return 1.0 if self.play(*win) == A else 0.0
            mine = self.pieces[self.to_move]
            n = len(mine)
            # campionamento per rifiuto: uniforme sulle mosse legali
            for _ in range(8 * n * 6):
                x = randrange(n * 6)
                src, d = mine[x // 6], x % 6
                kind = self.is_legal(src, d)
                if kind:
                    break
            else:
                ms = self.moves()
                if not ms:
                    self.pass_turn()
                    continue
                src, d, kind = ms[randrange(len(ms))]
            w = self.play(src, d, kind)
            if w:
                return 1.0 if w == A else 0.0
        if self.ply >= max_plies:
            return 0.5
        return self.advantage_value()

    def advantage_value(self):
        cells, R, N = self.geo.cells, self.rules.R, self.rules.N
        diff = sum(cells[i][1] + R for i in self.pieces[A]) - \
            sum(R - cells[i][1] for i in self.pieces[B])
        return 0.5 + diff / (4 * R * N)


class Node:
    """Nodo dell'albero: solo la mossa che porta qui, le statistiche e lo stato di fine.

    move: (cella, direzione, tipo) di FastBoard, None per il passo (None alla radice).
    terminal: valore per A se la partita è finita qui, altrimenti None.
    win_move: se chi muove ha una vittoria immediata il nodo è deciso e non si
    espande (è un fatto delle regole, non un'euristica).
    """

    __slots__ = ("parent", "move", "mover", "children", "untried", "visits", "value",
                 "terminal", "win_move")

    def __init__(self, parent, move, mover):
        self.parent = parent
        self.move = move
        self.mover = mover  # chi ha giocato `move` (A o B)
        self.children = []
        self.untried = []
        self.visits = 0
        self.value = 0.0  # somma dei risultati dal punto di vista di chi ha mosso per arrivare qui
        self.terminal = None
        self.win_move = None

    def open(self, fb, rng):
        """Prepara le mosse del nodo, se la partita non è finita qui."""
        self.win_move = fb.winning_move()
        if self.win_move is None:
            self.untried = fb.moves() or [None]
            rng.shuffle(self.untried)


def replay(fb, move):
    """Applica la mossa di un nodo a fb; restituisce il vincitore (1/2) o 0."""
    if move is None:
        fb.pass_turn()
        return 0
    return fb.play(*move)


def terminal_value(fb, winner, history, path):
    """Valore per A se la posizione appena raggiunta chiude la partita, altrimenti None.
    Stesso ordine di game._outcome: vittoria, poi ripetizione, poi limite di semimosse."""
    if winner:
        return 1.0 if winner == A else 0.0
    if history.get(fb.hash, 0) + path.get(fb.hash, 0) >= 3:
        return 0.5
    if fb.ply >= fb.rules.max_plies:
        return 0.5
    return None


@dataclass(frozen=True)
class TimePolicy:
    """Gestione del tempo: quante simulazioni fare in una posizione, dato il budget B
    (n_sim, oppure quelle che stanno in time_limit). Si decide ogni 64 simulazioni
    guardando solo le statistiche dei figli della radice: è un'euristica sul quanto
    pensare, non sulla mossa da scegliere.

    early: ci si ferma prima di B quando la mossa più visitata ha più visite della seconda
      di almeno early * (simulazioni che mancano). Con 1 è esatta: la prima non può più
      essere superata e la mossa scelta è la stessa. Con meno di 1 è più aggressiva.
      0 = mai.
    extend: arrivati a B si continua, fino a max_factor * B, se la ricerca è instabile:
      la più visitata non è la migliore per valore, oppure la seconda ha più di
      extend * le visite della prima. Durante l'estensione early vale rispetto al massimo.
      0 = mai.
    min_factor: mai meno di min_factor * B simulazioni.
    """
    early: float = 0.0
    extend: float = 0.0
    min_factor: float = 0.25
    max_factor: float = 3.0

    def stop(self, done, budget, stats):
        """True se la ricerca può fermarsi dopo `done` simulazioni. stats: (visite,
        somma dei valori) dei figli della radice, nell'ordine della ricerca."""
        if done < self.min_factor * budget or not stats:
            return False
        first, v2 = stats[0], 0
        for st in stats[1:]:
            if st[0] > first[0]:
                first, v2 = st, first[0]
            elif st[0] > v2:
                v2 = st[0]
        v1 = first[0]
        target = budget
        if done >= budget:
            if not self.extend or done >= self.max_factor * budget:
                return True
            best_q = max(st[1] / st[0] for st in stats if st[0])
            if first[1] / v1 >= best_q and v2 <= self.extend * v1:
                return True  # stabile
            target = self.max_factor * budget
        return self.early > 0 and v1 - v2 > self.early * (target - done)


# politiche da confrontare con thinktime.py match
POLICIES = {
    "early": TimePolicy(early=1.0),
    "stop50": TimePolicy(early=0.5),
    "unst": TimePolicy(early=1.0, extend=0.5),
    "stop50unst": TimePolicy(early=0.5, extend=0.5),
}


class MCTSBot:
    """MCTSBot(rng, n_sim[, playout_limit]). Spec: 'mcts:2000' o 'mcts:2000:40'.

    time_limit: secondi oltre i quali la ricerca si ferma anche prima di n_sim.
    policy: TimePolicy (o il suo nome in POLICIES); senza, si fanno sempre n_sim
    simulazioni (o fino a time_limit). Con time_limit il budget B è il numero di
    simulazioni che si stima stiano in time_limit, e il limite vero diventa
    max_factor * time_limit.
    """
    name = "mcts"

    decisive = False

    def __init__(self, rng=None, n_sim=500, playout_limit=None, c=1.4, time_limit=None,
                 policy=None):
        self.rng = rng or random.Random()
        self.n_sim = n_sim
        self.playout_limit = playout_limit
        self.c = c
        self.time_limit = time_limit
        self.policy = POLICIES[policy] if isinstance(policy, str) else policy
        # dopo ogni choose(): (n_figli, quota visite 1a, quota 2a, valore stimato 1a),
        # None se la mossa era obbligata o vincente
        self.last_stats = None
        self.last_sims = 0  # simulazioni fatte nell'ultima ricerca

    def choose(self, state):
        self.last_stats = None
        self.last_sims = 0
        moves = legal_moves(state)
        if len(moves) == 1:
            return moves[0]
        win, children = self.search(state)
        if win is not None:
            return win
        ranked = sorted(children, key=lambda ch: ch[1], reverse=True)
        total = sum(ch[1] for ch in ranked)
        second = ranked[1][1] if len(ranked) > 1 else 0
        self.last_stats = (len(ranked), ranked[0][1] / total, second / total,
                           ranked[0][2] / ranked[0][1])
        return ranked[0][0]

    def search(self, state):
        """Ricerca dalla posizione `state` (non finita, con almeno una mossa).
        Restituisce (mossa vincente, None) se chi muove vince subito, altrimenti
        (None, [(mossa, visite, somma dei valori per chi muove), ...]) per i figli della
        radice, con le mosse nel formato di game.Move (None per il passo)."""
        rng = self.rng
        root_fb = FastBoard(state)
        geo = root_fb.geo
        history = {}
        for key in state.history:
            h = geo.hash_key(key)
            history[h] = history.get(h, 0) + 1
        root = Node(None, None, 0)
        root.open(root_fb, rng)
        if root.win_move is not None:
            return self._to_move(geo, root.win_move), None
        c, limit, decisive = self.c, self.playout_limit, self.decisive
        sqrt, log = math.sqrt, math.log
        policy, budget, n_max = self.policy, self.n_sim, self.n_sim
        start = time.perf_counter()
        # solo l'estensione può superare il budget
        stretch = policy.max_factor if policy is not None and policy.extend else 1
        n_max = int(stretch * self.n_sim)
        deadline = None if self.time_limit is None else start + stretch * self.time_limit
        i = 0
        for i in range(n_max):
            if i & 63 == 63:
                if deadline is not None and time.perf_counter() > deadline:
                    break
                if policy is not None:
                    if self.time_limit is not None:
                        rate = i / max(time.perf_counter() - start, 1e-9)
                        budget = min(self.n_sim, rate * self.time_limit)
                    if policy.stop(i, budget, [(ch.visits, ch.value) for ch in root.children]):
                        break
            fb = root_fb.copy()
            path = {}
            node = root
            # selezione: si scende rigiocando le mosse
            while not node.untried and node.children and node.win_move is None \
                    and node.terminal is None:
                log_n = log(node.visits)
                best, best_u = None, -1.0
                for ch in node.children:
                    u = ch.value / ch.visits + c * sqrt(log_n / ch.visits)
                    if u > best_u:
                        best, best_u = ch, u
                node = best
                replay(fb, node.move)
                path[fb.hash] = path.get(fb.hash, 0) + 1
            # espansione
            if node.untried and node.terminal is None:
                move = node.untried.pop()
                child = Node(node, move, fb.to_move)
                winner = replay(fb, move)
                path[fb.hash] = path.get(fb.hash, 0) + 1
                child.terminal = terminal_value(fb, winner, history, path)
                if child.terminal is None:
                    child.open(fb, rng)
                node.children.append(child)
                node = child
            # simulazione
            if node.terminal is not None:
                value_a = node.terminal
            elif node.win_move is not None:
                value_a = 1.0 if fb.to_move == A else 0.0
            else:
                value_a = fb.random_playout(rng, limit, decisive)
            # retropropagazione
            while node is not None:
                node.visits += 1
                node.value += value_a if node.mover == A else 1.0 - value_a
                node = node.parent
        else:
            i = n_max
        self.last_sims = i
        return None, [(self._to_move(geo, ch.move), ch.visits, ch.value)
                      for ch in root.children]

    @staticmethod
    def _to_move(geo, move):
        if move is None:
            return None
        src, d, kind = move
        return Move(*geo.cells[src], d, kind >= 2)


class MCTSDecisiveBot(MCTSBot):
    """Variante tarata: mosse decisive nei playout e c=0.5. Spec: 'mctsd:2000'.

    Con c=1.4 e playout casuali le stime dei figli differiscono di pochi punti e le
    visite restano quasi uniformi (la mossa scelta prende ~10% delle visite).
    """
    name = "mctsd"
    decisive = True

    def __init__(self, rng=None, n_sim=500, playout_limit=None, c=0.5, time_limit=None,
                 policy=None):
        super().__init__(rng, n_sim, playout_limit, c, time_limit, policy)
