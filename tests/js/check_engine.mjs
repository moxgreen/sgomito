// Controlla il motore JavaScript del browser contro i casi preparati da
// tests/test_js_engine.py (JSON su stdin). Scrive su stdout {"errors": [...], "checked": n}.
//
//   node tests/js/check_engine.mjs < casi.json

import { FastBoard, fastGeometry, historyCounts, policyStop, replay, search, terminalValue }
  from "../../web/play/static/play/mcts.js";
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
  const opts = { nSim: c.n_sim, seed: c.seed, policy: c.policy || null };
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

// La decisione "fermati / continua" è la stessa di mcts.TimePolicy.stop.
function checkPolicy(c, where) {
  for (const [n, t] of c.tests.entries()) {
    const got = policyStop(t.policy, t.done, t.budget, t.stats);
    check(got === t.stop, `${where} #${n}`, "fermati", got, t.stop);
  }
}

let input = "";
for await (const chunk of process.stdin) input += chunk;
for (const [n, c] of JSON.parse(input).entries()) {
  const where = `${c.kind} ${n} ${c.label || ""}`.trim();
  if (c.kind === "geometry") checkGeometry(c, where);
  else if (c.kind === "game") checkGame(c, where);
  else if (c.kind === "search") checkSearch(c, where);
  else if (c.kind === "policy") checkPolicy(c, where);
  else errors.push({ where, what: "caso sconosciuto" });
}
process.stdout.write(JSON.stringify({ errors, checked }));
