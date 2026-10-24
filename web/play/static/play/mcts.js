// Ricerca del computer nel browser: porting di hexpush/mcts.py (FastBoard e MCTS con
// mosse decisive e c = 0.5, cioè MCTSDecisiveBot). Le regole di riferimento restano in
// hexpush/game.py: questo file ne è una copia veloce, tenuta allineata da
// tests/test_js_engine.py. Nomi e commenti seguono mcts.py per confrontarli riga per riga.
//
// Lo stato arriva dal server (serialize.search_state_to_dict). Differenze dal Python:
// - gli hash di Zobrist hanno 53 bit, in due metà (gli XOR del JavaScript sono a 32 bit):
//   hh con 21 bit e hl con 32; la chiave numerica è hh * 2^32 + hl;
// - i numeri casuali vengono da sfc32 con un seme, non dal Mersenne Twister di Python.

export const DIRS = [[1, 0], [-1, 0], [0, 1], [0, -1], [1, -1], [-1, 1]];
export const OPPOSITE = [1, 0, 3, 2, 5, 4];
export const A = 1, B = 2;
const CODE = { A, B };
const TWO32 = 4294967296;
const HI = 2097152;  // 2^21

const combine = (hh, hl) => hh * TWO32 + (hl >>> 0);

// --- numeri casuali ------------------------------------------------------------------

/** Generatore sfc32 inizializzato con splitmix32: rng() dà un numero in [0, 1). */
export function makeRng(seed) {
  let s = seed >>> 0;
  const split = () => {
    s = (s + 0x9e3779b9) | 0;
    let z = s;
    z = Math.imul(z ^ (z >>> 16), 0x85ebca6b);
    z = Math.imul(z ^ (z >>> 13), 0xc2b2ae35);
    return (z ^ (z >>> 16)) >>> 0;
  };
  let a = split(), b = split(), c = split(), d = split();
  const rng = () => {
    const t = (((a + b) | 0) + d) | 0;
    d = (d + 1) | 0;
    a = b ^ (b >>> 9);
    b = (c + (c << 3)) | 0;
    c = (c << 21) | (c >>> 11);
    c = (c + t) | 0;
    return (t >>> 0) / TWO32;
  };
  for (let i = 0; i < 12; i++) rng();
  return rng;
}

function shuffle(list, rng) {
  for (let i = list.length - 1; i > 0; i--) {
    const j = (rng() * (i + 1)) | 0;
    [list[i], list[j]] = [list[j], list[i]];
  }
}

// --- geometria -----------------------------------------------------------------------

function onBoard(R, q, r) {
  return Math.abs(q) <= R && Math.abs(r) <= R && Math.abs(q + r) <= R;
}

/** Come board.Geometry + mcts.FastGeo: celle indicizzate in ordine (q, r).
 * winDirs[p][i]: direzioni in cui una pedina di p in i può portare una pedina sul
 * traguardo in una mossa (muovendosi, o spingendo una propria pedina). */
export class FastGeo {
  constructor(R, goalWide) {
    this.cells = [];
    for (let q = -R; q <= R; q++) {
      for (let r = -R; r <= R; r++) if (onBoard(R, q, r)) this.cells.push([q, r]);
    }
    this.n = this.cells.length;
    this.index = new Map(this.cells.map(([q, r], i) => [`${q},${r}`, i]));
    this.nbr = this.cells.map(([q, r]) => DIRS.map(([dq, dr]) => this.at(q + dq, r + dr)));
    // traguardo di A: la fila di casa di B (r = R); di B: quella di A (r = -R).
    // goal_wide aggiunge le celle dei bordi laterali accanto agli angoli della casa nemica
    const wide = { [A]: [[-R, R - 1], [1, R - 1]], [B]: [[R, 1 - R], [-1, 1 - R]] };
    this.goal = [null];
    for (const p of [A, B]) {
      const g = new Uint8Array(this.n);
      this.cells.forEach(([, r], i) => { if (r === (p === A ? R : -R)) g[i] = 1; });
      if (goalWide) for (const [q, r] of wide[p]) g[this.at(q, r)] = 1;
      this.goal.push(g);
    }
    this.winDirs = [null];
    for (const p of [A, B]) {
      const goal = this.goal[p];
      this.winDirs.push(this.nbr.map((around) => {
        const ds = [];
        for (let d = 0; d < 6; d++) {
          const t = around[d];
          if (t < 0) continue;
          const t2 = this.nbr[t][d];
          if (goal[t] || (t2 >= 0 && goal[t2])) ds.push(d);
        }
        return ds;
      }));
    }
    const zr = makeRng(7919 * R + 1);
    const hi = () => (zr() * HI) | 0, lo = () => (zr() * TWO32) | 0;
    const table = (f) => Int32Array.from({ length: this.n }, f);
    this.zh = [null, table(hi), table(hi)];
    this.zl = [null, table(lo), table(lo)];
    this.sideH = hi(); this.sideL = lo();       // presente quando muove B
    this.lockH = table(hi); this.lockL = table(lo);  // pedina bloccata nel turno
    this.twoH = hi(); this.twoL = lo();         // presente quando restano 2 mosse nel turno
  }

  /** Indice della cella (q, r), -1 se fuori campo. */
  at(q, r) {
    const i = this.index.get(`${q},${r}`);
    return i === undefined ? -1 : i;
  }

  /** Le due metà dell'hash di una chiave [celle, to_move, locked, moves_left]
   * (game.position_key, in JSON). */
  hashParts([cells, toMove, locked, movesLeft]) {
    let h = 0, l = 0;
    if (toMove === "B") { h ^= this.sideH; l ^= this.sideL; }
    if (locked !== null) {
      const i = this.at(...locked);
      h ^= this.lockH[i]; l ^= this.lockL[i];
    }
    if (movesLeft === 2) { h ^= this.twoH; l ^= this.twoL; }
    for (const [q, r, owner] of cells) {
      const i = this.at(q, r), p = CODE[owner];
      h ^= this.zh[p][i]; l ^= this.zl[p][i];
    }
    return [h, l];
  }

  hashKey(key) {
    return combine(...this.hashParts(key));
  }
}

const GEOMETRIES = new Map();

export function fastGeometry(R, goalWide) {
  const k = `${R},${goalWide}`;
  if (!GEOMETRIES.has(k)) GEOMETRIES.set(k, new FastGeo(R, goalWide));
  return GEOMETRIES.get(k);
}

// --- campo ---------------------------------------------------------------------------

/** Copia mutabile di uno stato per i playout. `s` viene da search_state_to_dict. */
export class FastBoard {
  constructor(s) {
    if (s === undefined) return;  // copy()
    const rules = s.rules;
    const g = fastGeometry(rules.R, rules.goal_wide);
    this.geo = g;
    this.rules = rules;
    this.board = new Int8Array(g.n);
    this.pieces = [null, [], []];
    this.pid = new Int16Array(g.n);
    this.goalCount = [0, 0, 0];
    for (const [q, r, owner] of s.board) {
      const i = g.at(q, r), p = CODE[owner];
      this.board[i] = p;
      this.pid[i] = this.pieces[p].length;
      this.pieces[p].push(i);
      this.goalCount[p] += g.goal[p][i];
    }
    this.toMove = CODE[s.to_move];
    this.ply = s.ply;
    this.movesLeft = s.moves_left;
    this.locked = s.locked === null ? -1 : g.at(...s.locked);
    [this.hh, this.hl] = g.hashParts([s.board, s.to_move, s.locked, s.moves_left]);
    // ko: hash d'inizio del turno corrente e del precedente (quello vietato a fine turno)
    const tk = s.turn_keys;
    this.turnStart = g.hashKey(tk[tk.length - 1]);
    this.koKey = rules.ko && tk.length >= 2 ? g.hashKey(tk[tk.length - 2]) : null;
    // no_pushback: spinte inverse come cella * 6 + direzione (vedi GameState)
    this.turnPushes = s.turn_pushes.map(([q, r, d]) => g.at(q, r) * 6 + d);
    this.banned = s.banned.map(([q, r, d]) => g.at(q, r) * 6 + d);
  }

  copy() {
    const fb = new FastBoard();
    fb.geo = this.geo; fb.rules = this.rules;
    fb.board = this.board.slice(); fb.pid = this.pid.slice();
    fb.pieces = [null, this.pieces[A].slice(), this.pieces[B].slice()];
    fb.goalCount = this.goalCount.slice();
    fb.toMove = this.toMove; fb.ply = this.ply; fb.hh = this.hh; fb.hl = this.hl;
    fb.movesLeft = this.movesLeft; fb.locked = this.locked;
    fb.turnStart = this.turnStart; fb.koKey = this.koKey;
    // le liste di spinte non si modificano mai sul posto: si possono condividere
    fb.turnPushes = this.turnPushes; fb.banned = this.banned;
    return fb;
  }

  /** Hash della posizione come numero (chiave per le ripetizioni). */
  key() {
    return combine(this.hh, this.hl);
  }

  /** src: cella con una pedina di chi muove. Restituisce 0 illegale, 1 semplice,
   * 2 spinta, 3 spinta fuori dal campo (push_off). */
  isLegal(src, d) {
    if (src === this.locked) return 0;
    const g = this.geo;
    const t = g.nbr[src][d];
    if (t < 0) return 0;
    const o = this.board[t];
    if (o === 0) return 1;
    if (o === this.toMove && !this.rules.push_own) return 0;
    const t2 = g.nbr[t][d];
    if (t2 < 0) return this.rules.push_off && o !== this.toMove ? 3 : 0;
    if (this.board[t2]) return 0;
    if (this.banned.length && o !== this.toMove && this.banned.includes(src * 6 + d)) return 0;
    if (this.koKey !== null && this.movesLeft === 1) {
      // hash della posizione d'inizio del turno avversario che questa mossa creerebbe
      const p = this.toMove;
      let h = this.hh ^ g.zh[p][src] ^ g.zh[p][t] ^ g.zh[o][t] ^ g.zh[o][t2] ^ g.sideH;
      let l = this.hl ^ g.zl[p][src] ^ g.zl[p][t] ^ g.zl[o][t] ^ g.zl[o][t2] ^ g.sideL;
      if (this.locked >= 0) { h ^= g.lockH[this.locked]; l ^= g.lockL[this.locked]; }
      if (this.rules.moves_per_turn === 2) { h ^= g.twoH; l ^= g.twoL; }
      if (combine(h, l) === this.koKey) return 0;
    }
    return 2;
  }

  /** Lista di [cella, direzione, tipo] legali. */
  moves() {
    const out = [];
    for (const src of this.pieces[this.toMove]) {
      for (let d = 0; d < 6; d++) {
        const k = this.isLegal(src, d);
        if (k) out.push([src, d, k]);
      }
    }
    return out;
  }

  movePiece(frm, to) {
    const p = this.board[frm], k = this.pid[frm];
    this.board[frm] = 0;
    this.board[to] = p;
    this.pid[to] = k;
    this.pieces[p][k] = to;
    const g = this.geo.goal[p];
    this.goalCount[p] += g[to] - g[frm];
    this.hh ^= this.geo.zh[p][frm] ^ this.geo.zh[p][to];
    this.hl ^= this.geo.zl[p][frm] ^ this.geo.zl[p][to];
  }

  removePiece(cell) {
    const p = this.board[cell], k = this.pid[cell];
    const mine = this.pieces[p];
    const last = mine.pop();
    if (last !== cell) {
      mine[k] = last;
      this.pid[last] = k;
    }
    this.board[cell] = 0;
    this.goalCount[p] -= this.geo.goal[p][cell];
    this.hh ^= this.geo.zh[p][cell];
    this.hl ^= this.geo.zl[p][cell];
  }

  /** Applica una mossa legale; restituisce il vincitore (1/2) o 0. */
  play(src, d, kind) {
    const me = this.toMove, g = this.geo, nbr = g.nbr;
    const dst = nbr[src][d];
    if (kind === 3) this.removePiece(dst);
    else if (kind === 2) {
      if (this.rules.no_pushback && this.board[dst] !== me) {
        this.turnPushes = this.turnPushes.concat(nbr[dst][d] * 6 + OPPOSITE[d]);
      }
      this.movePiece(dst, nbr[dst][d]);
    }
    this.movePiece(src, dst);
    this.ply += 1;
    if (this.movesLeft > 1) {
      // stesso turno: la pedina appena mossa resta ferma
      this.movesLeft = 1;
      this.locked = dst;
      this.hh ^= g.twoH ^ g.lockH[dst];
      this.hl ^= g.twoL ^ g.lockL[dst];
    } else this.endTurn();
    const K = this.rules.K;
    if (this.goalCount[me] >= K) return me;
    if (this.goalCount[3 - me] >= K) return 3 - me;
    if (kind === 3 && this.pieces[3 - me].length < K) return me;
    return 0;
  }

  endTurn() {
    const g = this.geo;
    if (this.locked >= 0) {
      this.hh ^= g.lockH[this.locked];
      this.hl ^= g.lockL[this.locked];
      this.locked = -1;
    }
    this.toMove = 3 - this.toMove;
    this.hh ^= g.sideH;
    this.hl ^= g.sideL;
    this.movesLeft = this.rules.moves_per_turn;
    if (this.movesLeft === 2) { this.hh ^= g.twoH; this.hl ^= g.twoL; }
    if (this.rules.ko) this.koKey = this.turnStart;
    this.turnStart = this.key();
    this.banned = this.turnPushes;
    this.turnPushes = [];
  }

  /** Il passo chiude sempre il turno. */
  passTurn() {
    this.ply += 1;
    this.endTurn();
  }

  /** Una mossa che fa vincere subito chi muove, o null. */
  winningMove() {
    const me = this.toMove, g = this.geo;
    const winDirs = g.winDirs[me], goal = g.goal[me], nbr = g.nbr;
    const need = this.rules.K - this.goalCount[me];
    for (const src of this.pieces[me]) {
      for (const d of winDirs[src]) {
        const kind = this.isLegal(src, d);
        if (!kind) continue;
        const dst = nbr[src][d];
        let gain = goal[dst] - goal[src];
        if (kind === 2 && this.board[dst] === me) gain += goal[nbr[dst][d]] - goal[dst];
        if (gain >= need) return [src, d, kind];
      }
    }
    if (this.rules.push_off && this.pieces[3 - me].length <= this.rules.K) {
      // eliminare una pedina avversaria basta a vincere
      for (const m of this.moves()) if (m[2] === 3) return m;
    }
    return null;
  }

  /** Gioca a caso fino alla fine (o per `limit` semimosse). Con decisive chi muove
   * sceglie una vittoria immediata quando c'è. Restituisce il valore per A in [0, 1]:
   * 1 vittoria, 0 sconfitta, 0.5 patta per max_plies; se troncato, 0.5 + differenza
   * di avanzamento normalizzata. */
  randomPlayout(rng, limit = null, decisive = false) {
    const maxPlies = this.rules.max_plies;
    const stop = limit === null ? maxPlies : Math.min(maxPlies, this.ply + limit);
    while (this.ply < stop) {
      if (decisive) {
        const win = this.winningMove();
        if (win !== null) return this.play(...win) === A ? 1 : 0;
      }
      const mine = this.pieces[this.toMove];
      const n = mine.length;
      // campionamento per rifiuto: uniforme sulle mosse legali
      let src = -1, d = 0, kind = 0;
      for (let tries = 8 * n * 6; tries > 0; tries--) {
        const x = (rng() * n * 6) | 0;
        src = mine[(x / 6) | 0];
        d = x % 6;
        kind = this.isLegal(src, d);
        if (kind) break;
      }
      if (!kind) {
        const ms = this.moves();
        if (!ms.length) {
          this.passTurn();
          continue;
        }
        [src, d, kind] = ms[(rng() * ms.length) | 0];
      }
      const w = this.play(src, d, kind);
      if (w) return w === A ? 1 : 0;
    }
    if (this.ply >= maxPlies) return 0.5;
    return this.advantageValue();
  }

  advantageValue() {
    const cells = this.geo.cells, R = this.rules.R, N = this.rules.N;
    let diff = 0;
    for (const i of this.pieces[A]) diff += cells[i][1] + R;
    for (const i of this.pieces[B]) diff -= R - cells[i][1];
    return 0.5 + diff / (4 * R * N);
  }
}

// --- albero --------------------------------------------------------------------------

/** Nodo dell'albero: solo la mossa che porta qui, le statistiche e lo stato di fine.
 * move: [cella, direzione, tipo], null per il passo (null alla radice).
 * terminal: valore per A se la partita è finita qui, altrimenti null.
 * winMove: se chi muove ha una vittoria immediata il nodo è deciso e non si espande. */
class TreeNode {
  constructor(parent, move, mover) {
    this.parent = parent;
    this.move = move;
    this.mover = mover;  // chi ha giocato `move` (A o B)
    this.children = [];
    this.untried = [];
    this.visits = 0;
    this.value = 0;  // somma dei risultati dal punto di vista di chi ha mosso per arrivare qui
    this.terminal = null;
    this.winMove = null;
  }

  /** Prepara le mosse del nodo, se la partita non è finita qui. */
  open(fb, rng) {
    this.winMove = fb.winningMove();
    if (this.winMove === null) {
      this.untried = fb.moves();
      if (!this.untried.length) this.untried.push(null);
      shuffle(this.untried, rng);
    }
  }
}

/** Applica la mossa di un nodo a fb; restituisce il vincitore (1/2) o 0. */
export function replay(fb, move) {
  if (move === null) {
    fb.passTurn();
    return 0;
  }
  return fb.play(move[0], move[1], move[2]);
}

/** Valore per A se la posizione appena raggiunta chiude la partita, altrimenti null.
 * Stesso ordine di game._outcome: vittoria, poi ripetizione, poi limite di semimosse. */
export function terminalValue(fb, winner, history, path) {
  if (winner) return winner === A ? 1 : 0;
  const k = fb.key();
  if ((history.get(k) || 0) + (path.get(k) || 0) >= 3) return 0.5;
  if (fb.ply >= fb.rules.max_plies) return 0.5;
  return null;
}

/** Occorrenze di ogni posizione della storia della partita, per hash. */
export function historyCounts(geo, keys) {
  const history = new Map();
  for (const key of keys) {
    const h = geo.hashKey(key);
    history.set(h, (history.get(h) || 0) + 1);
  }
  return history;
}

/** Gestione del tempo, come mcts.TimePolicy.stop: true se la ricerca può fermarsi dopo
 * `done` simulazioni con budget `budget`. stats: [visite, somma dei valori] dei figli
 * della radice, nell'ordine della ricerca. policy: {early, extend, min_factor, max_factor}. */
export function policyStop(policy, done, budget, stats) {
  if (done < policy.min_factor * budget || !stats.length) return false;
  let first = stats[0], v2 = 0;
  for (let k = 1; k < stats.length; k++) {
    const st = stats[k];
    if (st[0] > first[0]) { v2 = first[0]; first = st; }
    else if (st[0] > v2) v2 = st[0];
  }
  const v1 = first[0];
  let target = budget;
  if (done >= budget) {
    if (!policy.extend || done >= policy.max_factor * budget) return true;
    let bestQ = -Infinity;
    for (const st of stats) if (st[0] && st[1] / st[0] > bestQ) bestQ = st[1] / st[0];
    if (first[1] / v1 >= bestQ && v2 <= policy.extend * v1) return true;  // stabile
    target = policy.max_factor * budget;
  }
  return policy.early > 0 && v1 - v2 > policy.early * (target - done);
}

const toMove = (geo, m) => (m === null ? null : [...geo.cells[m[0]], m[1]]);

/** Ricerca MCTS dalla posizione `s` (non finita). Come MCTSBot.search, ma restituisce
 * {move} se la mossa è obbligata o fa vincere subito, altrimenti
 * {children: [[mossa, visite, somma dei valori per chi muove], ...], sims}, con le mosse
 * come [q, r, d] (null per il passo).
 * timeLimit: secondi oltre i quali la ricerca si ferma anche prima di nSim.
 * policy: gestione del tempo (policyStop) o null; con timeLimit il budget è il numero di
 * simulazioni che si stima stiano in timeLimit. */
export function search(s, { nSim = 500, timeLimit = null, seed = 0, c = 0.5, decisive = true,
  playoutLimit = null, policy = null } = {}) {
  const rng = makeRng(seed);
  const rootFb = new FastBoard(s);
  const geo = rootFb.geo;
  const history = historyCounts(geo, s.history);
  const root = new TreeNode(null, null, 0);
  root.open(rootFb, rng);
  if (root.winMove !== null) return { move: toMove(geo, root.winMove) };
  if (root.untried.length === 1) return { move: toMove(geo, root.untried[0]) };
  // solo l'estensione può superare il budget
  const stretch = policy && policy.extend ? policy.max_factor : 1;
  const nMax = Math.floor(stretch * nSim);
  const start = performance.now();
  const deadline = timeLimit === null ? null : start + stretch * timeLimit * 1000;
  let budget = nSim;
  let i = 0;
  for (; i < nMax; i++) {
    if ((i & 63) === 63) {
      if (deadline !== null && performance.now() > deadline) break;
      if (policy) {
        if (timeLimit !== null) {
          const rate = (i * 1000) / Math.max(performance.now() - start, 1e-6);
          budget = Math.min(nSim, rate * timeLimit);
        }
        if (policyStop(policy, i, budget, root.children.map((ch) => [ch.visits, ch.value]))) break;
      }
    }
    const fb = rootFb.copy();
    const path = new Map();
    let node = root;
    // selezione: si scende rigiocando le mosse
    while (!node.untried.length && node.children.length && node.winMove === null &&
           node.terminal === null) {
      const logN = Math.log(node.visits);
      let best = null, bestU = -1;
      for (const ch of node.children) {
        const u = ch.value / ch.visits + c * Math.sqrt(logN / ch.visits);
        if (u > bestU) { best = ch; bestU = u; }
      }
      node = best;
      replay(fb, node.move);
      path.set(fb.key(), (path.get(fb.key()) || 0) + 1);
    }
    // espansione
    if (node.untried.length && node.terminal === null) {
      const move = node.untried.pop();
      const child = new TreeNode(node, move, fb.toMove);
      const winner = replay(fb, move);
      path.set(fb.key(), (path.get(fb.key()) || 0) + 1);
      child.terminal = terminalValue(fb, winner, history, path);
      if (child.terminal === null) child.open(fb, rng);
      node.children.push(child);
      node = child;
    }
    // simulazione
    let valueA;
    if (node.terminal !== null) valueA = node.terminal;
    else if (node.winMove !== null) valueA = fb.toMove === A ? 1 : 0;
    else valueA = fb.randomPlayout(rng, playoutLimit, decisive);
    // retropropagazione
    for (; node !== null; node = node.parent) {
      node.visits += 1;
      node.value += node.mover === A ? valueA : 1 - valueA;
    }
  }
  return {
    children: root.children.map((ch) => [toMove(geo, ch.move), ch.visits, ch.value]),
    sims: i,
  };
}
