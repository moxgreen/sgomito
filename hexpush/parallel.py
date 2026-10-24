"""MCTS parallelo alla radice: più alberi indipendenti in processi separati.

Ogni processo fa una ricerca MCTSDecisiveBot completa dalla stessa posizione con un
seed diverso; il padre somma, mossa per mossa, le visite dei figli della radice e
sceglie la più visitata. Nessuna memoria condivisa, nessuna euristica nuova: è solo
più calcolo nello stesso tempo.

I processi sono persistenti (si avviano alla prima mossa e restano vivi) e usano
pypy3 se c'è, perché i playout sono ~8x più veloci che con CPython; il server web
può restare in CPython. Si parlano con pickle su stdin/stdout: sono processi nostri,
lanciati con questo stesso codice.

    python -m hexpush.parallel        # processo di lavoro (lo lancia WorkerPool)
"""
import atexit
import os
import pickle
import random
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from .game import GameState, legal_moves
from .mcts import MCTSDecisiveBot

ROOT = Path(__file__).resolve().parent.parent

# campi di GameState che viaggiano verso i processi, nell'ordine di __init__
# (geo si ricalcola dalle regole)
FIELDS = tuple(f for f in GameState.__slots__ if f != "geo")
PROTOCOL = 4  # lo capiscono sia CPython sia PyPy


def default_python():
    """pypy3 se c'è (HEXPUSH_PYPY per indicarne uno preciso), altrimenti questo Python."""
    return os.environ.get("HEXPUSH_PYPY") or shutil.which("pypy3") or sys.executable


def default_workers():
    """Metà dei core logici, al massimo 8: sui Mac con core efficienti (o con
    l'hyperthreading) processi in più aggiungono poche simulazioni e alberi più piccoli."""
    return max(1, min((os.cpu_count() or 2) // 2, 8))


class WorkerPool:
    """Processi di lavoro persistenti. Una ricerca alla volta: chi arriva mentre
    un'altra partita sta pensando aspetta il suo turno."""

    def __init__(self, workers, python):
        self.workers = workers
        self.python = python
        self.procs = []
        self.lock = threading.Lock()

    def _start(self):
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        self.procs = [subprocess.Popen([self.python, "-m", "hexpush.parallel"],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       cwd=ROOT, env=env)
                      for _ in range(self.workers)]

    def search(self, state, n_sim, time_limit, seeds):
        """Una ricerca per processo; restituisce i risultati di MCTSBot.search."""
        fields = tuple(getattr(state, f) for f in FIELDS)
        with self.lock:
            if not self.procs or any(p.poll() is not None for p in self.procs):
                self.close()
                self._start()
            try:
                for proc, seed in zip(self.procs, seeds):
                    pickle.dump((fields, n_sim, time_limit, seed), proc.stdin, PROTOCOL)
                    proc.stdin.flush()
                return [pickle.load(proc.stdout) for proc in self.procs]
            except Exception:
                self.close()  # i processi ripartono alla prossima ricerca
                raise

    def close(self):
        for proc in self.procs:
            try:
                proc.stdin.close()
                proc.wait(timeout=2)
            except Exception:
                proc.kill()
        self.procs = []


_POOLS = {}
_POOLS_LOCK = threading.Lock()


def get_pool(workers, python):
    with _POOLS_LOCK:
        key = (workers, python)
        if key not in _POOLS:
            _POOLS[key] = WorkerPool(workers, python)
        return _POOLS[key]


@atexit.register
def _close_pools():
    for pool in _POOLS.values():
        pool.close()


class ParallelMCTSBot:
    """ParallelMCTSBot(rng, n_sim, workers): `workers` ricerche MCTSDecisiveBot da
    n_sim simulazioni ciascuna. Spec: 'mctsp:20000' o 'mctsp:20000:8'.

    time_limit ferma ogni ricerca dopo tanti secondi (riparo se manca pypy3).
    """
    name = "mctsp"

    def __init__(self, rng=None, n_sim=10000, workers=None, time_limit=None, python=None):
        self.rng = rng or random.Random()
        self.n_sim = n_sim
        self.workers = workers or default_workers()
        self.time_limit = time_limit
        self.python = python or default_python()
        self.last_stats = None

    def choose(self, state):
        self.last_stats = None
        moves = legal_moves(state)
        if len(moves) == 1:
            return moves[0]
        seeds = [self.rng.getrandbits(64) for _ in range(self.workers)]
        pool = get_pool(self.workers, self.python)
        results = pool.search(state, self.n_sim, self.time_limit, seeds)
        visits, values = {}, {}
        for win, children in results:
            if win is not None:
                return win
            for move, n, v in children:
                visits[move] = visits.get(move, 0) + n
                values[move] = values.get(move, 0.0) + v
        # a parità di visite conta il valore; poi l'ordine di legal_moves
        order = {m: i for i, m in enumerate(moves)}
        ranked = sorted(visits, key=lambda m: (-visits[m], -values[m], order[m]))
        total = sum(visits.values())
        best = ranked[0]
        second = visits[ranked[1]] if len(ranked) > 1 else 0
        self.last_stats = (len(ranked), visits[best] / total, second / total,
                           values[best] / visits[best])
        return best


def worker():
    """Ciclo del processo di lavoro: legge richieste finché stdin non si chiude."""
    inp, out = sys.stdin.buffer, sys.stdout.buffer
    while True:
        try:
            fields, n_sim, time_limit, seed = pickle.load(inp)
        except EOFError:
            return
        state = GameState(*fields)
        bot = MCTSDecisiveBot(random.Random(seed), n_sim, time_limit=time_limit)
        pickle.dump(bot.search(state), out, PROTOCOL)
        out.flush()


if __name__ == "__main__":
    worker()
