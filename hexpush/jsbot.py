"""Bot che cerca col motore del browser (web/play/static/play/mcts.js) in node.

La partita la gioca game.py (il motore di riferimento); a ogni mossa lo stato va a un
processo node (hexpush/jsserver.mjs) che fa la ricerca e restituisce le visite dei figli
della radice. Serve per il corpus della policy e per misurarla (hexpush/policy.py): il
motore JS è ~4x più veloce di pypy, e così si misura proprio il codice del sito.
"""
import atexit
import json
import shutil
import subprocess
from pathlib import Path

from .game import PASS, Move, legal_moves
from .serialize import search_state_to_dict

SERVER = Path(__file__).resolve().parent / "jsserver.mjs"


class JsEngine:
    """Un processo node con mcts.js, riusato per tutte le richieste."""

    def __init__(self):
        node = shutil.which("node")
        if node is None:
            raise RuntimeError("serve node (>= 22) per il motore JavaScript")
        self.proc = subprocess.Popen([node, str(SERVER)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True, bufsize=1)
        atexit.register(self.close)

    def request(self, **req):
        self.proc.stdin.write(json.dumps(req, separators=(",", ":")) + "\n")
        self.proc.stdin.flush()
        out = json.loads(self.proc.stdout.readline())
        if "error" in out:
            raise RuntimeError(out["error"])
        return out

    def search(self, state, **opts):
        return self.request(op="search", state=search_state_to_dict(state), opts=opts)

    def features(self, state):
        return self.request(op="features", state=search_state_to_dict(state))

    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            self.proc.wait()


_ENGINE = None


def engine():
    """Il processo node di questo processo Python (uno per processo del pool)."""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = JsEngine()
    return _ENGINE


def to_move(state, m):
    """Mossa [q, r, d] del JS (None per il passo) come game.Move."""
    if m is None:
        return PASS
    return next(x for x in legal_moves(state) if x is not PASS and [x.q, x.r, x.d] == m)


class JsBot:
    """MCTS di mcts.js (mosse decisive, c = 0.5 come mctsd) con n_sim simulazioni e un
    albero solo; opts va a search() così com'è (per esempio prior=True).
    Dopo choose(): last_stats come MCTSBot, last_children con le visite della radice
    ([[q, r, d], visite, valore] o None se la mossa era obbligata o vincente), last_ms."""

    def __init__(self, rng, n_sim=2000, **opts):
        self.rng = rng
        self.n_sim = n_sim
        self.opts = opts
        self.last_stats = self.last_children = None
        self.last_ms = 0.0
        self.last_sims = 0

    def choose(self, state):
        self.last_stats = self.last_children = None
        seed = self.rng.getrandbits(32)
        res = engine().search(state, nSim=self.n_sim, seed=seed, **self.opts)
        self.last_ms = res["ms"]
        if "move" in res:
            self.last_sims = 0
            return to_move(state, res["move"])
        children = res["children"]
        self.last_children = children
        self.last_sims = res["sims"]
        # come computer.combine: più visite, poi valore, poi ordine
        best = max(range(len(children)), key=lambda k: (children[k][1], children[k][2], -k))
        ranked = sorted((ch[1] for ch in children), reverse=True)
        total = sum(ranked)
        top = children[best]
        self.last_stats = (len(children), ranked[0] / total,
                           (ranked[1] if len(ranked) > 1 else 0) / total, top[2] / top[1])
        return to_move(state, top[0])
