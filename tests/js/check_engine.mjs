// Controlla il motore JavaScript del browser contro i casi preparati da
// tests/test_js_engine.py (JSON su stdin). Scrive su stdout {"errors": [...], "checked": n}.
//
//   node tests/js/check_engine.mjs < casi.json

import { FEATURE_NAMES, FastBoard, fastGeometry, historyCounts, moveFeatures, movePriors,
  policyScale, policyStop, replay, search, terminalValue } from "../../web/play/static/play/mcts.js";
import { combine } from "../../web/play/static/play/computer.js";

const NAME = [null, "A", "B"];
const VALUE = { A: 1, B: 0, draw: 0.5 };

const errors = [];
let checked = 0;

function check(cond, where, what, got, want) {
  checked++;
  if (!cond && errors.length < 50) errors.push({ where, what, got, want });
}

const sameJson = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const sorted = (list) => list.map((x) => JSON.stringify(x)).sort();

function movesOf(fb) {
  return fb.moves().map(([src, d, kind]) => [...fb.geo.cells[src], d, kind >= 2]);
}

function boardOf(fb) {
  const out = [];
  fb.board.forEach((p, i) => { if (p) out.push([...fb.geo.cells[i], NAME[p]]); });
  return out;
}

function cellOf(fb, i) {
  return i < 0 ? null : fb.geo.cells[i];
}

function checkGeometry(c, where) {
  const g = fastGeometry(c.R, c.goal_wide);
  check(sameJson(sorted(g.cells), sorted(c.cells)), where, "celle", g.cells.length, c.cells.length);
  for (const [p, name] of [[1, "A"], [2, "B"]]) {
    const goal = g.cells.filter((_, i) => g.goal[p][i]);
    check(sameJson(sorted(goal), sorted(c.goal[name])), where, `traguardo ${name}`, goal, c.goal[name]);
  }
}

// Partita casuale giocata in parallelo: a ogni semimossa stesse mosse legali, stesse
// vittorie immediate, stesso campo e stesso esito (anche ripetizione e limite di semimosse,
// contati come nell'albero: storia della partita + percorso).
function checkGame(c, where) {
  const fb = new FastBoard(c.start);
  const history = historyCounts(fb.geo, c.start.history);
  const path = new Map();
  c.steps.forEach((st, n) => {
    const at = `${where} semimossa ${c.start.ply + n}`;
    check(sameJson(sorted(movesOf(fb)), sorted(st.legal)), at, "mosse legali",
      movesOf(fb).length, st.legal.length);
    const win = fb.winningMove();
    if (st.wins.length) {
      const got = win && [...fb.geo.cells[win[0]], win[1]];
      check(got && st.wins.some((w) => sameJson(w, got)), at, "mossa vincente", got, st.wins);
    } else check(win === null, at, "nessuna mossa vincente", win, null);
    let move = null;
    if (st.move !== null) {
      const src = fb.geo.at(st.move[0], st.move[1]);
      move = [src, st.move[2], fb.isLegal(src, st.move[2])];
    }
    const winner = replay(fb, move);
    path.set(fb.key(), (path.get(fb.key()) || 0) + 1);
    const value = terminalValue(fb, winner, history, path);
    check(value === (st.result === null ? null : VALUE[st.result]), at, "esito", value, st.result);
    check(sameJson(sorted(boardOf(fb)), sorted(st.board)), at, "campo", boardOf(fb), st.board);
    check(NAME[fb.toMove] === st.to_move && fb.movesLeft === st.moves_left &&
      sameJson(cellOf(fb, fb.locked), st.locked), at, "turno",
      [NAME[fb.toMove], fb.movesLeft, cellOf(fb, fb.locked)], [st.to_move, st.moves_left, st.locked]);
    if (st.state) {
      // ricostruito da zero dallo stato del server: stesso hash, stesso ko, stesse spinte
      // vietate, e la storia contata come quella accumulata giocando
      const fresh = new FastBoard(st.state);
      check(fresh.key() === fb.key() && fresh.turnStart === fb.turnStart &&
        fresh.koKey === fb.koKey, at, "hash ricostruito", fresh.key(), fb.key());
      check(sameJson([...fresh.banned].sort(), [...fb.banned].sort()) &&
        sameJson([...fresh.turnPushes].sort(), [...fb.turnPushes].sort()), at, "spinte vietate",
        fresh.banned, fb.banned);
      const counts = historyCounts(fresh.geo, st.state.history);
      const total = new Map(history);
      for (const [k, v] of path) total.set(k, (total.get(k) || 0) + v);
      check(sameJson([...counts].sort(), [...total].sort()), at, "storia", counts.size, total.size);
    }
  });
}

// Ricerca: la mossa scelta deve stare tra quelle accettabili; le visite dei figli della
// radice sommano al numero di simulazioni; stesso seme, stesso risultato.
// Con una gestione del tempo le simulazioni restano tra min e max (e B senza estensione).
function checkSearch(c, where) {
  const opts = { nSim: c.n_sim, seed: c.seed, policy: c.policy || null, ...(c.opts || {}) };
  const res = search(c.state, opts);
  const move = combine([res]);
  check(c.ok.some((m) => sameJson(m, move)), where, "mossa scelta", move, c.ok);
  if (res.children) {
    const visits = res.children.reduce((s, ch) => s + ch[1], 0);
    const [lo, hi] = c.sims || [c.n_sim, c.n_sim];
    check(visits === res.sims && lo <= res.sims && res.sims <= hi, where, "visite",
      [visits, res.sims], [lo, hi]);
    check(sameJson(search(c.state, opts), res), where, "determinismo", null, null);
  }
}

// Riuso dell'albero lungo una partita, con lo stesso keep: i figli della radice sono mosse
// legali della posizione nuova, le visite sono ereditate + nuove, il riuso scatta almeno
// c.min_reused volte e mai sulle posizioni marcate `fresh` (non discendono dalla precedente).
function checkReuse(c, where) {
  const keep = {};
  let reusedCount = 0;
  for (const [k, st] of c.steps.entries()) {
    const at = `${where} #${k}`;
    const res = search(st.state, { nSim: c.n_sim, seed: k, keep, ...(c.opts || {}) });
    const legal = st.legal.map((m) => JSON.stringify(m));
    check(legal.includes(JSON.stringify(combine([res]))), at, "mossa scelta", combine([res]), null);
    if (!res.children) continue;
    const visits = res.children.reduce((s, ch) => s + ch[1], 0);
    check(visits === res.reused + res.sims && res.sims === c.n_sim, at, "visite",
      [visits, res.reused, res.sims], c.n_sim);
    const bad = res.children.filter((ch) => !legal.includes(JSON.stringify(ch[0])));
    check(!bad.length && res.children.length <= legal.length, at, "figli legali", bad, null);
    if (st.fresh) check(res.reused === 0, at, "riuso su posizione scollegata", res.reused, 0);
    if (res.reused > 0) reusedCount++;
  }
  check(reusedCount >= c.min_reused, where, "ricerche con riuso", reusedCount, c.min_reused);
}

// La decisione "fermati / continua" è la stessa di mcts.TimePolicy.stop, e il fattore
// di fase lo stesso di TimePolicy.scale.
function checkPolicy(c, where) {
  for (const [n, t] of c.tests.entries()) {
    const got = policyStop(t.policy, t.done, t.budget, t.stats);
    check(got === t.stop, `${where} #${n}`, "fermati", got, t.stop);
  }
  for (const [n, t] of (c.scales || []).entries()) {
    const got = policyScale(t.policy, new FastBoard(t.state));
    check(got === t.scale, `${where} fase #${n}`, "fattore", got, t.scale);
  }
}

// Feature della policy: i valori attesi (calcolati con game.py) per le mosse elencate,
// una sola direzione / distanza di partenza / distanza d'arrivo per mossa, e movePriors
// uguale al softmax di pesi · feature.
function checkFeatures(c, where) {
  const fb = new FastBoard(c.state);
  const moves = fb.moves();
  const feats = moveFeatures(fb, moves);
  const named = feats.map((f) => Object.fromEntries(FEATURE_NAMES.map((n, k) => [n, f[k]])));
  const keys = moves.map(([src, d]) => JSON.stringify([...fb.geo.cells[src], d]));
  check(FEATURE_NAMES.length === c.n_features, where, "numero di feature", FEATURE_NAMES.length,
    c.n_features);
  for (const [k, f] of named.entries()) {
    for (const prefix of ["dir_", "src_d", "dst_d"]) {
      const ones = FEATURE_NAMES.filter((n) => n.startsWith(prefix)).reduce((s, n) => s + f[n], 0);
      check(ones === 1, `${where} ${keys[k]}`, `una sola ${prefix}`, ones, 1);
    }
  }
  for (const e of c.expect) {
    const k = keys.indexOf(JSON.stringify(e.move));
    check(k >= 0, where, "mossa presente", e.move, null);
    if (k < 0) continue;
    for (const [name, want] of Object.entries(e.values)) {
      check(named[k][name] === want, `${where} ${keys[k]}`, name, named[k][name], want);
    }
  }
  const w = Float64Array.from(c.weights);
  const pr = movePriors(fb, moves, w, c.temp);
  const z = feats.map((f) => f.reduce((s, x, k) => s + x * w[k], 0) / c.temp);
  const top = Math.max(...z);
  const tot = z.reduce((s, x) => s + Math.exp(x - top), 0);
  const ok = z.every((x, j) => Math.abs(Math.exp(x - top) / tot - pr[j]) < 1e-12);
  check(ok && Math.abs(pr.reduce((a, b) => a + b, 0) - 1) < 1e-12, where, "movePriors",
    Array.from(pr), z);
}

let input = "";
for await (const chunk of process.stdin) input += chunk;
for (const [n, c] of JSON.parse(input).entries()) {
  const where = `${c.kind} ${n} ${c.label || ""}`.trim();
  if (c.kind === "geometry") checkGeometry(c, where);
  else if (c.kind === "game") checkGame(c, where);
  else if (c.kind === "search") checkSearch(c, where);
  else if (c.kind === "policy") checkPolicy(c, where);
  else if (c.kind === "features") checkFeatures(c, where);
  else if (c.kind === "reuse") checkReuse(c, where);
  else errors.push({ where, what: "caso sconosciuto" });
}
process.stdout.write(JSON.stringify({ errors, checked }));
