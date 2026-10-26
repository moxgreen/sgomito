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
    // distanza dal traguardo in file (feature della policy)
    this.dist = [null, Int8Array.from(this.cells, ([, r]) => R - r),
      Int8Array.from(this.cells, ([, r]) => r + R)];
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
    let t2 = -1;
    if (o !== 0) {
      if (o === this.toMove && !this.rules.push_own) return 0;
      t2 = g.nbr[t][d];
      if (t2 < 0) return this.rules.push_off && o !== this.toMove ? 3 : 0;
      if (this.board[t2]) return 0;
      if (this.banned.length && o !== this.toMove && this.banned.includes(src * 6 + d)) return 0;
    }
    if (this.koKey !== null && this.movesLeft === 1) {
      // hash della posizione d'inizio del turno avversario che questa mossa creerebbe;
      // anche una mossa semplice: con due mosse per turno può chiudere una spinta
      // che rimette il campo com'era (spinta indietro con un'altra pedina + ritorno)
      const p = this.toMove;
      let h = this.hh ^ g.zh[p][src] ^ g.zh[p][t] ^ g.sideH;
      let l = this.hl ^ g.zl[p][src] ^ g.zl[p][t] ^ g.sideL;
      if (o !== 0) {
        h ^= g.zh[o][t] ^ g.zh[o][t2];
        l ^= g.zl[o][t] ^ g.zl[o][t2];
      }
      if (this.locked >= 0) { h ^= g.lockH[this.locked]; l ^= g.lockL[this.locked]; }
      if (this.rules.moves_per_turn === 2) { h ^= g.twoH; l ^= g.twoL; }
      if (combine(h, l) === this.koKey) return 0;
    }
    return o !== 0 ? 2 : 1;
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

// --- policy --------------------------------------------------------------------------

/** Nomi delle feature di una mossa (moveFeatures), dal punto di vista di chi muove.
 * dist = distanza dal proprio traguardo in file (src prima della mossa, dst dopo);
 * enemy_* descrive la pedina avversaria spinta; vul = pedine nemiche che possono
 * spingere la pedina; end_* valgono se la mossa chiude il turno, mid_* se ne resta
 * un'altra; *_win = qualcuno avrebbe una vittoria immediata dopo la mossa. */
export const FEATURE_NAMES = [
  "dir_fwd", "dir_side", "dir_back",
  ...[1, 2, 3, 4, 5, 6, 7, 8].map((k) => `src_d${k}`),
  ...[0, 1, 2, 3, 4, 5, 6, 7, 8].map((k) => `dst_d${k}`),
  "push_own", "push_enemy", "own_pushed_fwd",
  "enemy_back", "enemy_side", "enemy_fwd", "enemy_d1", "enemy_d2", "enemy_d3p",
  "adj_enemy", "adj_own", "vul_dst", "vul_src", "loses",
  "end_opp_win", "end_my_threats", "mid_my_win", "mid_opp_win",
];
const F = Object.fromEntries(FEATURE_NAMES.map((name, k) => [name, k]));

/** Pesi di movePriors dai pesi lineari (nell'ordine di FEATURE_NAMES) e da quelli delle
 * coppie di feature, come [a, b, peso, ...] con a < b. */
function priorWeights(linear, pairs) {
  const dim = FEATURE_NAMES.length;
  const w = new Float64Array(pairs.length ? dim + dim * dim : dim);
  w.set(linear);
  for (let k = 0; k < pairs.length; k += 3) w[dim + pairs[k] * dim + pairs[k + 1]] = pairs[k + 2];
  return w;
}

/** Pesi della policy: hexpush/policy.py train --pairs --install su
 * results/policy_corpus_jsp20000.jsonl (100 partite di mcts.js a 20000 simulazioni guidate
 * dalla policy precedente, con le regole del sito). Valgono per quelle regole (R = 4,
 * N = 5, due mosse per turno). */
export const PRIOR_WEIGHTS = priorWeights([
  0.4573, -0.1197, -0.6795, 0.287, 0.0257, 0.0509, 0.0658, 0.1247, 0.023, -0.1933,
  -0.2773, 0, 0.6764, 0.3243, -0.1295, -0.3314, -0.4102, -0.3697, -0.1907, 0.0286,
  0.3441, 0.2106, 0.4882, 0.1947, 0.1201, -0.0227, 0.1674, 0.3393, -0.0163, 0.1105,
  0.061, -0.0722, 0.1074, -0.1179, -1.3924, 0.2377, 0.4229, -1.3083,
], [
  0, 4, 0.438, 0, 5, 0.4275, 0, 6, 0.4543, 0, 7, 0.2844, 0, 8, 0.0035, 0, 9, -0.1515,
  0, 10, -0.2872, 0, 12, 0.438, 0, 13, 0.4275, 0, 14, 0.4543, 0, 15, 0.2844,
  0, 16, 0.0035, 0, 17, -0.1515, 0, 18, -0.2872, 0, 20, 0.4882, 0, 21, 0.1947,
  0, 22, 0.4882, 0, 23, 0.1947, 0, 26, 0.1868, 0, 27, 0.3711, 0, 28, -0.0891,
  0, 29, -0.0985, 0, 30, 0.1163, 0, 31, -0.0874, 0, 32, 0.0904, 0, 34, -0.5818,
  0, 35, -0.1223, 0, 36, 0.1369, 0, 37, -0.8112, 1, 3, 0.5172, 1, 4, 0.0387,
  1, 5, -0.1238, 1, 6, -0.1449, 1, 7, -0.0871, 1, 8, -0.1389, 1, 9, -0.1198,
  1, 10, -0.0808, 1, 12, 0.5172, 1, 13, 0.0387, 1, 14, -0.1238, 1, 15, -0.1449,
  1, 16, -0.0871, 1, 17, -0.1389, 1, 18, -0.1198, 1, 19, -0.0808, 1, 20, 0.0652,
  1, 21, 0.1201, 1, 24, 0.1201, 1, 26, 0.0727, 1, 27, 0.0503, 1, 28, 0.0217,
  1, 29, 0.3003, 1, 30, -0.0194, 1, 31, 0.0475, 1, 32, -0.1707, 1, 34, -0.4587,
  1, 35, 0.1748, 1, 36, 0.286, 1, 37, -0.8193, 2, 3, -0.2339, 2, 4, -0.5534,
  2, 5, -0.4526, 2, 6, -0.3416, 2, 7, -0.0853, 2, 8, 0.2048, 2, 9, 0.1654,
  2, 13, -0.2339, 2, 14, -0.5534, 2, 15, -0.4526, 2, 16, -0.3416, 2, 17, -0.0853,
  2, 18, 0.2048, 2, 19, 0.1654, 2, 20, -0.2525, 2, 21, -0.0227, 2, 25, -0.0227,
  2, 26, -0.1179, 2, 27, -0.061, 2, 28, 0.1561, 2, 29, 0.6834, 2, 30, -0.0756,
  2, 31, 0.0138, 2, 32, 0.1335, 2, 33, -0.1179, 2, 34, -0.4468, 2, 35, 0.257,
  2, 37, -0.2435, 3, 12, 0.5172, 3, 13, -0.2339, 3, 20, 0.0312, 3, 21, 0.2111,
  3, 24, -0.1011, 3, 25, 0.3087, 3, 28, 0.2111, 3, 29, -0.4973, 3, 30, -0.1228,
  3, 31, -0.0808, 3, 32, -0.5439, 3, 34, -0.0362, 3, 35, 0.0671, 3, 36, 0.286,
  3, 37, -0.0553, 4, 12, 0.438, 4, 13, 0.0387, 4, 14, -0.5534, 4, 20, -0.038,
  4, 21, -0.0533, 4, 23, -0.1339, 4, 24, 0.0871, 4, 25, -0.0168, 4, 28, -0.0533,
  4, 29, -0.0634, 4, 30, 0.3312, 4, 31, -0.1927, 4, 32, 0.1366, 4, 34, -0.1401,
  4, 35, 0.1652, 4, 36, 0.0169, 4, 37, -0.2853, 5, 13, 0.4275, 5, 14, -0.1238,
  5, 15, -0.4526, 5, 20, -0.0662, 5, 21, -0.3073, 5, 22, 0.1136, 5, 23, -0.3426,
  5, 24, 0.0804, 5, 25, -0.0424, 5, 28, -0.3073, 5, 29, 0.1209, 5, 30, 0.3113,
  5, 31, 0.0567, 5, 32, 0.3232, 5, 34, -0.1582, 5, 35, 0.2411, 5, 36, 0.1203,
  5, 37, -0.0696, 6, 14, 0.4543, 6, 15, -0.1449, 6, 16, -0.3416, 6, 20, 0.0258,
  6, 21, -0.2211, 6, 22, -0.013, 6, 23, -0.2029, 6, 24, 0.0777, 6, 25, -0.0925,
  6, 28, -0.2211, 6, 29, 0.3622, 6, 30, -0.1359, 6, 31, 0.2215, 6, 32, 0.3558,
  6, 34, -0.203, 6, 35, -0.0205, 6, 37, -0.3978, 7, 15, 0.2844, 7, 16, -0.0871,
  7, 17, -0.0853, 7, 20, 0.0337, 7, 21, -0.0925, 7, 22, 0.2732, 7, 23, 0.1119,
  7, 24, -0.1392, 7, 25, -0.061, 7, 27, -0.061, 7, 28, -0.0299, 7, 29, 0.5454,
  7, 30, -0.096, 7, 31, 0.1209, 7, 32, 0.0147, 7, 34, -0.3094, 7, 35, 0.0307,
  7, 37, -0.3639, 8, 16, 0.0035, 8, 17, -0.1389, 8, 18, 0.2048, 8, 20, 0.2089,
  8, 21, 0.3027, 8, 22, 0.2022, 8, 23, 0.3752, 8, 24, 0.0503, 8, 25, -0.1179,
  8, 26, -0.1179, 8, 27, 0.0503, 8, 28, 0.3752, 8, 29, 0.453, 8, 30, -0.1617,
  8, 31, 0.0533, 8, 32, 0.0754, 8, 33, -0.1179, 8, 34, -0.2612, 8, 35, -0.2192,
  8, 37, -0.3677, 9, 17, -0.1515, 9, 18, -0.1198, 9, 19, 0.1654, 9, 20, 0.1981,
  9, 21, 0.4094, 9, 22, 0.0787, 9, 23, 0.3711, 9, 24, 0.0727, 9, 26, 0.0727,
  9, 27, 0.3711, 9, 29, -0.1704, 9, 30, 0.0401, 9, 31, 0.0839, 9, 32, -0.1688,
  9, 34, -0.204, 9, 35, -0.005, 9, 37, -0.1431, 10, 18, -0.2872, 10, 19, -0.0808,
  10, 20, 0.0001, 10, 21, 0.1868, 10, 22, -0.1036, 10, 23, 0.1868, 10, 26, 0.1868,
  10, 29, 0.017, 10, 30, -0.0262, 10, 31, -0.4128, 10, 34, -0.1601, 10, 35, 0.0058,
  10, 37, -0.1646, 12, 20, 0.1748, 12, 21, -0.1981, 12, 23, -0.1339, 12, 24, -0.1011,
  12, 28, -0.1981, 12, 29, 0.097, 12, 30, 0.0695, 12, 31, 0.0821, 12, 32, 0.2526,
  12, 34, -0.1451, 12, 35, 0.2803, 12, 36, 0.3026, 12, 37, -0.0783, 13, 20, -0.0396,
  13, 21, 0.0418, 13, 22, 0.1136, 13, 23, -0.3426, 13, 24, 0.0871, 13, 25, 0.3087,
  13, 28, 0.0418, 13, 29, 0.0014, 13, 30, -0.0934, 13, 31, 0.0537, 13, 32, 0.1222,
  13, 34, -0.2797, 13, 35, 0.082, 13, 36, 0.1203, 13, 37, -0.4059, 14, 20, -0.1303,
  14, 21, -0.1397, 14, 22, -0.013, 14, 23, -0.2029, 14, 24, 0.0804, 14, 25, -0.0168,
  14, 28, -0.1397, 14, 29, 0.0455, 14, 30, 0.113, 14, 31, -0.2344, 14, 32, -0.3006,
  14, 34, -0.1943, 14, 35, -0.109, 14, 37, -0.4396, 15, 20, 0.2444, 15, 21, 0.1466,
  15, 22, 0.2732, 15, 23, 0.1119, 15, 24, 0.0777, 15, 25, -0.0424, 15, 28, 0.1466,
  15, 29, -0.4558, 15, 30, 0.1648, 15, 31, -0.0654, 15, 32, -0.3692, 15, 34, -0.2442,
  15, 35, -0.0779, 15, 37, -0.2279, 16, 20, -0.0977, 16, 21, 0.1741, 16, 22, 0.2022,
  16, 23, 0.3752, 16, 24, -0.1392, 16, 25, -0.0925, 16, 28, 0.1741, 16, 29, -0.3002,
  16, 30, 0.1195, 16, 31, -0.3836, 16, 32, -0.192, 16, 34, 0.0265, 16, 35, 0.0216,
  16, 37, -0.0361, 17, 20, 0.1204, 17, 21, 0.3393, 17, 22, 0.0787, 17, 23, 0.3711,
  17, 24, 0.0503, 17, 25, -0.061, 17, 27, 0.3393, 17, 29, -0.1972, 17, 30, 0.1492,
  17, 31, -0.1584, 17, 32, -0.1143, 17, 34, 0.1716, 17, 35, -0.0125, 17, 37, -0.1206,
  18, 20, -0.0067, 18, 21, 0.1674, 18, 22, -0.1036, 18, 23, 0.1868, 18, 24, 0.0727,
  18, 25, -0.1179, 18, 26, 0.1674, 18, 29, 0.2755, 18, 30, -0.0915, 18, 31, 0.2524,
  18, 32, 0.1132, 18, 33, -0.1179, 18, 34, -0.2551, 18, 35, 0.0026, 18, 37, -0.2045,
  19, 20, 0.0903, 19, 29, 0.9071, 19, 30, -0.1659, 19, 32, 0.4918, 19, 34, -0.5507,
  19, 35, 0.0537, 19, 37, -0.3244, 20, 22, 0.4882, 20, 29, 0.6408, 20, 30, -0.0606,
  20, 31, -0.002, 20, 32, -0.438, 20, 34, -0.1959, 20, 35, 0.0084, 20, 36, 0.1699,
  20, 37, -0.1525, 21, 23, 0.1947, 21, 24, 0.1201, 21, 25, -0.0227, 21, 26, 0.1674,
  21, 27, 0.3393, 21, 28, -0.0163, 21, 29, -0.0888, 21, 30, 0.0487, 21, 31, 0.1368,
  21, 32, -0.0258, 21, 33, -0.1179, 21, 34, -0.2605, 21, 35, -0.261, 21, 36, 0.0302,
  21, 37, -0.0201, 22, 29, -0.8068, 22, 30, -0.3531, 22, 31, -0.3137, 22, 32, -0.1544,
  22, 34, 0.0513, 22, 35, -0.0067, 22, 36, 0.1203, 22, 37, -0.0998, 23, 26, 0.1868,
  23, 27, 0.3711, 23, 28, -0.0891, 23, 29, -0.0236, 23, 30, -0.0407, 23, 31, 0.1163,
  23, 32, 0.0441, 23, 34, -0.0952, 23, 35, -0.0395, 23, 36, -0.0167, 23, 37, 0.2502,
  24, 26, 0.0727, 24, 27, 0.0503, 24, 28, 0.0217, 24, 29, -0.1327, 24, 30, 0.103,
  24, 31, 0.2246, 24, 32, -0.0914, 24, 34, -0.1827, 24, 35, -0.2134, 24, 36, 0.0465,
  24, 37, -0.2185, 25, 26, -0.1179, 25, 27, -0.061, 25, 28, 0.1561, 25, 29, -0.1806,
  25, 30, 0.1495, 25, 31, -0.1462, 25, 32, -0.0696, 25, 33, -0.1179, 25, 34, 0.0171,
  25, 35, -0.0341, 25, 37, -0.1043, 26, 29, -0.0068, 26, 30, -0.0295, 26, 31, 0.176,
  26, 32, -0.1244, 26, 33, -0.1179, 26, 34, -0.0024, 26, 35, 0.1976, 26, 37, 0.5329,
  27, 29, -0.337, 27, 30, -0.0114, 27, 31, -0.1557, 27, 32, 0.3961, 27, 34, -0.1873,
  27, 35, -0.3686, 27, 37, -0.3044, 28, 29, 0.0243, 28, 30, 0.1451, 28, 31, 0.1789,
  28, 32, -0.1713, 28, 34, -0.0726, 28, 35, -0.1075, 28, 36, 0.0302, 28, 37, -0.2845,
  29, 30, -0.0161, 29, 31, -0.1002, 29, 32, -0.0587, 29, 33, -0.1078, 29, 34, -0.5007,
  29, 35, -0.1318, 29, 36, 0.4287, 29, 37, -0.1504, 30, 31, 0.0062, 30, 32, 0.0117,
  30, 33, -0.0468, 30, 34, -0.2201, 30, 35, 0.0986, 30, 36, 0.3871, 30, 37, -0.2726,
  31, 32, 0.1121, 31, 33, -0.1179, 31, 34, 0.0119, 31, 35, 0.1356, 31, 36, 0.354,
  31, 37, 0.0366, 32, 33, -0.1536, 32, 34, -0.2963, 32, 35, -0.1121, 32, 36, 0.4294,
  32, 37, -0.3423, 33, 34, 0.0312, 33, 35, 0.0083, 33, 37, -0.097, 34, 35, -0.3678,
  36, 37, 0.3898,
]);

/** Pedine di o che potrebbero spingere la pedina in x (legalità semplificata: senza ko
 * né no_pushback). */
function pushers(fb, x, o) {
  const nbr = fb.geo.nbr, board = fb.board;
  let n = 0;
  for (let e = 0; e < 6; e++) {
    const y = nbr[x][e];
    if (y < 0 || board[y] !== o) continue;
    const behind = nbr[x][OPPOSITE[e]];
    if (behind >= 0 && board[behind] === 0) n++;
  }
  return n;
}

/** Mosse di p che vincerebbero subito, contate fino a `cap` (legalità semplificata come
 * pushers; la pedina in `locked` non muove). */
function countWins(fb, p, locked, cap) {
  const g = fb.geo, nbr = g.nbr, goal = g.goal[p], board = fb.board;
  const need = fb.rules.K - fb.goalCount[p];
  let n = 0;
  for (const src of fb.pieces[p]) {
    if (src === locked) continue;
    for (const d of g.winDirs[p][src]) {
      const t = nbr[src][d];
      let gain = goal[t] - goal[src];
      if (board[t]) {
        if (board[t] === p && !fb.rules.push_own) continue;
        const t2 = nbr[t][d];
        if (t2 < 0 || board[t2]) continue;
        if (board[t] === p) gain += goal[t2] - goal[t];
      }
      if (gain >= need && ++n >= cap) return n;
    }
  }
  return n;
}

/** Sposta una pedina solo su campo, pedine e traguardi (senza hash): per provare una
 * mossa e disfarla in moveFeatures. */
function shift(fb, frm, to) {
  const p = fb.board[frm], k = fb.pid[frm], g = fb.geo.goal[p];
  fb.board[frm] = 0;
  fb.board[to] = p;
  fb.pid[to] = k;
  fb.pieces[p][k] = to;
  fb.goalCount[p] += g[to] - g[frm];
}

/** Scrive in f (azzerato) le feature della mossa legale (src, d, kind) di chi muove. */
function featuresInto(fb, src, d, kind, f) {
  const p = fb.toMove, o = 3 - p, nbr = fb.geo.nbr, sign = p === A ? 1 : -1;
  const dr = DIRS[d][1] * sign;
  f[dr > 0 ? F.dir_fwd : dr < 0 ? F.dir_back : F.dir_side] = 1;
  const dist = fb.geo.dist;
  f[F.src_d1 + Math.min(Math.max(dist[p][src], 1), 8) - 1] = 1;
  const dst = nbr[src][d];
  f[F.dst_d0 + Math.min(dist[p][dst], 8)] = 1;
  f[F.vul_src] = pushers(fb, src, o);
  if (kind >= 2) {
    if (fb.board[dst] === p) {
      f[F.push_own] = 1;
      if (dr > 0) f[F.own_pushed_fwd] = 1;
    } else {
      f[F.push_enemy] = 1;
      // per la pedina avversaria "avanti" è verso la mia casa
      f[dr > 0 ? F.enemy_back : dr < 0 ? F.enemy_fwd : F.enemy_side] = 1;
      const ed = dist[o][dst];
      f[ed <= 1 ? F.enemy_d1 : ed === 2 ? F.enemy_d2 : F.enemy_d3p] = 1;
    }
  }
  // la mossa si prova sul campo e poi si disfa (con push_off su una copia)
  const t2 = kind === 2 ? nbr[dst][d] : -1;
  const after = kind === 3 ? fb.copy() : fb;
  if (kind === 3) after.removePiece(dst);
  else if (kind === 2) shift(fb, dst, t2);
  shift(after, src, dst);
  if (after.goalCount[o] >= fb.rules.K) f[F.loses] = 1;
  for (const y of nbr[dst]) {
    if (y < 0) continue;
    if (after.board[y] === o) f[F.adj_enemy]++;
    else if (after.board[y] === p && y !== dst) f[F.adj_own]++;
  }
  f[F.vul_dst] = pushers(after, dst, o);
  if (fb.movesLeft === 1) {
    f[F.end_opp_win] = countWins(after, o, -1, 1);
    f[F.end_my_threats] = countWins(after, p, -1, 2);
  } else {
    f[F.mid_my_win] = countWins(after, p, dst, 1);
    f[F.mid_opp_win] = countWins(after, o, -1, 1);
  }
  if (kind !== 3) {
    shift(fb, dst, src);
    if (kind === 2) shift(fb, t2, dst);
  }
}

/** Feature di ogni mossa [cella, direzione, tipo] di fb.moves(): un array per mossa,
 * nell'ordine di FEATURE_NAMES. */
export function moveFeatures(fb, moves) {
  return moves.map(([src, d, kind]) => {
    const f = new Float64Array(FEATURE_NAMES.length);
    featuresInto(fb, src, d, kind, f);
    return f;
  });
}

const SCRATCH = new Float64Array(FEATURE_NAMES.length);
const NONZERO = new Int32Array(FEATURE_NAMES.length);

/** Probabilità della policy per le mosse di fb (softmax di pesi · feature, con
 * temperatura temp). Con dim feature, weights ha dim pesi, oppure dim + dim² se la policy
 * ha anche i pesi delle coppie di feature: quello della coppia a < b è in
 * weights[dim + a * dim + b] e moltiplica f[a] * f[b]. */
export function movePriors(fb, moves, weights = PRIOR_WEIGHTS, temp = 1) {
  const n = moves.length, out = new Float64Array(n), dim = SCRATCH.length;
  const pairs = weights.length > dim;
  let top = -Infinity;
  for (let j = 0; j < n; j++) {
    SCRATCH.fill(0);
    featuresInto(fb, moves[j][0], moves[j][1], moves[j][2], SCRATCH);
    let z = 0, nz = 0;
    for (let k = 0; k < dim; k++) {
      const x = SCRATCH[k];
      if (x) { z += weights[k] * x; NONZERO[nz++] = k; }
    }
    if (pairs) {
      for (let a = 0; a < nz; a++) {
        const ka = NONZERO[a], xa = SCRATCH[ka], row = dim + ka * dim;
        for (let b = a + 1; b < nz; b++) {
          const kb = NONZERO[b];
          z += weights[row + kb] * xa * SCRATCH[kb];
        }
      }
    }
    out[j] = z / temp;
    if (out[j] > top) top = out[j];
  }
  let sum = 0;
  for (let j = 0; j < n; j++) sum += (out[j] = Math.exp(out[j] - top));
  for (let j = 0; j < n; j++) out[j] /= sum;
  return out;
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
    this.prior = 0;  // probabilità della policy per `move` (solo con prior)
  }

  /** Prepara le mosse del nodo, se la partita non è finita qui. Senza `pw` le mosse
   * restano da provare in ordine casuale (UCT); con pw = {weights, temp} diventano
   * subito figli non visitati, ognuno con la sua probabilità della policy (PUCT). */
  open(fb, rng, pw = null) {
    this.winMove = fb.winningMove();
    if (this.winMove !== null) return;
    const moves = fb.moves();
    if (pw === null) {
      this.untried = moves.length ? moves : [null];
      shuffle(this.untried, rng);
      return;
    }
    const priors = moves.length ? movePriors(fb, moves, pw.weights, pw.temp) : [1];
    if (!moves.length) moves.push(null);
    this.children = moves.map((m, k) => {
      const ch = new TreeNode(this, m, fb.toMove);
      ch.prior = priors[k];
      return ch;
    });
  }
}

/** Figlio da seguire con PUCT: q + cPuct * prior * sqrt(visite del nodo) / (1 + visite),
 * con q = valore medio per chi muove nel nodo; i figli mai visitati valgono il valore
 * del nodo meno fpu. */
function selectPuct(node, cPuct, fpu) {
  let q0 = 0.5;
  if (node.visits) {
    q0 = node.value / node.visits;
    if (node.mover !== node.children[0].mover) q0 = 1 - q0;
  }
  q0 -= fpu;
  const sq = Math.sqrt(node.visits + 1);
  let best = null, bestU = -Infinity;
  for (const ch of node.children) {
    const q = ch.visits ? ch.value / ch.visits : q0;
    const u = q + (cPuct * ch.prior * sq) / (1 + ch.visits);
    if (u > bestU) { best = ch; bestU = u; }
  }
  return best;
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

/** File che mancano al traguardo alla pedina più vicina, di chiunque sia. */
export function goalDistance(fb) {
  const cells = fb.geo.cells, R = fb.rules.R;
  let d = Infinity;
  for (const i of fb.pieces[A]) d = Math.min(d, R - cells[i][1]);
  for (const i of fb.pieces[B]) d = Math.min(d, cells[i][1] + R);
  return d;
}

/** Fattore per il budget nella posizione fb, come mcts.TimePolicy.scale. */
export function policyScale(policy, fb) {
  if (!policy.phase || !policy.phase.length) return 1;
  return policy.phase[Math.min(goalDistance(fb), policy.phase.length) - 1];
}

const toMove = (geo, m) => (m === null ? null : [...geo.cells[m[0]], m[1]]);

/** Nodo della ricerca precedente (keep.tree) da cui ripartire nella posizione di `s`, o
 * null. Si scende dalla vecchia radice seguendo la storia della partita, al più
 * REUSE_DEPTH semimosse (le due del turno avversario e una propria). Il vecchio albero si
 * stacca comunque, così la memoria si libera. fresh: radice appena aperta su `s`, per
 * controllare che il nodo trovato abbia le stesse mosse. Un albero UCT non si riusa con
 * il prior né viceversa (prior: quello della ricerca nuova). */
const REUSE_DEPTH = 4;
function reusedRoot(keep, s, geo, fresh, prior) {
  const t = keep.tree;
  keep.tree = null;
  const hist = s.history, d = s.ply - t?.ply;
  if (!t || t.prior !== prior || t.rules !== JSON.stringify(s.rules) ||
      !(d >= 1 && d <= REUSE_DEPTH) ||
      d >= hist.length || geo.hashKey(hist[hist.length - 1 - d]) !== t.key) return null;
  let node = t.root;
  const fb = t.fb.copy();
  for (let j = d - 1; j >= 0 && node; j--) {
    const target = geo.hashKey(hist[hist.length - 1 - j]);
    const prev = node;
    node = null;
    for (const ch of prev.children) {
      const f = fb.copy();
      replay(f, ch.move);
      if (f.key() === target) {
        node = ch;
        replay(fb, ch.move);
        break;
      }
    }
  }
  if (!node || node.terminal !== null || node.winMove !== null ||
      node.children.length + node.untried.length !==
      fresh.children.length + fresh.untried.length) return null;
  node.parent = null;
  return node;
}

/** Ricerca MCTS dalla posizione `s` (non finita). Come MCTSBot.search, ma restituisce
 * {move} se la mossa è obbligata o fa vincere subito, altrimenti
 * {children: [[mossa, visite, somma dei valori per chi muove], ...], sims, reused}, con
 * le mosse come [q, r, d] (null per il passo).
 * timeLimit: secondi oltre i quali la ricerca si ferma anche prima di nSim.
 * policy: gestione del tempo (policyStop) o null; con timeLimit il budget è il numero di
 * simulazioni che si stima stiano in timeLimit.
 * keep: oggetto che il chiamante conserva tra una ricerca e l'altra della stessa partita
 * (dello stesso giocatore). Se `s` discende dalla posizione della ricerca precedente, si
 * riparte dal suo sottoalbero: `reused` sono le visite ereditate, e nSim (o il tempo)
 * conta solo le simulazioni nuove. Senza keep ogni ricerca parte da zero (reused 0).
 * prior: invece di UCT, PUCT con le probabilità della policy (movePriors) come prior;
 * cPuct, fpu e priorTemp ne sono i parametri, weights sostituisce PRIOR_WEIGHTS.
 * Spento di default: la ricerca pura è quella della curva a raddoppi. */
export function search(s, { nSim = 500, timeLimit = null, seed = 0, c = 0.5, decisive = true,
  playoutLimit = null, policy = null, keep = null, prior = false, cPuct = 2, fpu = 0,
  priorTemp = 1, weights = null } = {}) {
  const rng = makeRng(seed);
  const rootFb = new FastBoard(s);
  const geo = rootFb.geo;
  const history = historyCounts(geo, s.history);
  const pw = prior ? { weights: weights ? Float64Array.from(weights) : PRIOR_WEIGHTS,
    temp: priorTemp } : null;
  // con il prior la radice ha il valore per l'avversario di chi muove, come gli altri nodi
  let root = new TreeNode(null, null, prior ? 3 - rootFb.toMove : 0);
  root.open(rootFb, rng, pw);
  const old = keep ? reusedRoot(keep, s, geo, root, prior) : null;
  if (root.winMove !== null) return { move: toMove(geo, root.winMove) };
  const options = prior ? root.children.map((ch) => ch.move) : root.untried;
  if (options.length === 1) return { move: toMove(geo, options[0]) };
  if (old) root = old;
  // visite ereditate dai figli (la radice ne ha una in più: il suo primo playout)
  const reused = root.children.reduce((n, ch) => n + ch.visits, 0);
  // solo l'estensione può superare il budget
  const scale = policy ? policyScale(policy, rootFb) : 1;
  let budget = scale * nSim;
  const stretch = policy && policy.extend ? policy.max_factor : 1;
  const nMax = Math.floor(stretch * budget);
  const start = performance.now();
  const deadline = timeLimit === null ? null : start + stretch * scale * timeLimit * 1000;
  let i = 0;
  for (; i < nMax; i++) {
    if ((i & 63) === 63) {
      if (deadline !== null && performance.now() > deadline) break;
      if (policy) {
        if (timeLimit !== null) {
          const rate = (i * 1000) / Math.max(performance.now() - start, 1e-6);
          budget = scale * Math.min(nSim, rate * timeLimit);
        }
        if (policyStop(policy, i, budget, root.children.map((ch) => [ch.visits, ch.value]))) break;
      }
    }
    const fb = rootFb.copy();
    const path = new Map();
    let node = root;
    // selezione: si scende rigiocando le mosse
    while (prior && node.winMove === null && node.terminal === null) {
      // PUCT: il primo figlio mai visitato che si incontra è l'espansione
      node = selectPuct(node, cPuct, fpu);
      const winner = replay(fb, node.move);
      path.set(fb.key(), (path.get(fb.key()) || 0) + 1);
      if (node.visits === 0) {
        node.terminal = terminalValue(fb, winner, history, path);
        if (node.terminal === null) node.open(fb, rng, pw);
        break;
      }
    }
    while (!prior && !node.untried.length && node.children.length && node.winMove === null &&
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
  if (keep) keep.tree = { rules: JSON.stringify(s.rules), ply: s.ply, key: rootFb.key(),
    fb: rootFb, root, prior };
  return {
    children: root.children.filter((ch) => ch.visits)
      .map((ch) => [toMove(geo, ch.move), ch.visits, ch.value]),
    sims: i,
    reused,
  };
}
