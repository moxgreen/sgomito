// Pagina della partita. Nessuna regola qui: il server manda lo stato completo con le
// mosse legali (già con `to` e `pushed_to`); il browser disegna e sceglie tra quelle.
// Contro il computer è il browser a calcolare le mosse del computer (computer.js): lo
// stato contiene la richiesta `bot` e la risposta torna al server, che la valida.

const SVGNS = "http://www.w3.org/2000/svg";
const HEX = 40;                 // raggio di una cella, in unità SVG
const SQ3 = Math.sqrt(3);
// vicini in coordinate assiali: servono solo a disegnare i bordi del campo
const NEIGH = [[1, 0], [-1, 0], [0, 1], [0, -1], [1, -1], [-1, 1]];
const NAMES = { A: "Orange", B: "Blue" };
const REASONS = {
  goal: "piece on the goal",
  elimination: "opponent's pieces eliminated",
  repetition: "position repeated three times",
  max_plies: "ply limit reached",
  resign: "resignation",
};
const KO_TEXT = "Forbidden by the ko: it would recreate the position from the start of " +
  "the previous turn (you can't undo your opponent's move right away).";
const PUSHBACK_TEXT = "Forbidden: this piece has just been pushed and can't push back " +
  "the piece that pushed it.";

const $ = (id) => document.getElementById(id);
const root = $("game");
const svg = $("board");
const matchId = root.dataset.match;
const key = (q, r) => `${q},${r}`;

let S = null;            // ultimo stato ricevuto
let flip = false;        // true se chi guarda è A: la sua casa (r = -R) va in basso
let moves = new Map();   // "q,r" -> mosse legali di quella pedina
let koMoves = new Map(); // "q,r" -> mosse vietate dal ko o dalla regola della rispinta
let selected = null;     // "q,r" della pedina selezionata
let hoverDest = null;    // mossa sotto il puntatore (pedina selezionata)
let press = null;        // pressione in corso su una pedina: clic o trascinamento
let pendingDrop = null;  // dove è stata lasciata la pedina trascinata
let waiting = false;     // mossa mandata, in attesa dello stato
let pieceEls = new Map();
let layers = {};
let ws = null;
let retry = 0;
let hintTimer = null;
let ratingShown = false;  // il dialogo del giudizio si apre da solo una volta
let stars = 0;
let botPly = null;        // semimossa a cui il computer sta già rispondendo
// stessa versione dei file statici (?v=...) anche per i moduli caricati da qui
const computer = import(`./computer.js${new URL(import.meta.url).search}`)
  .then((m) => new m.Computer());

// --- geometria del disegno -----------------------------------------------------------

function px(q, r) {
  const x = HEX * SQ3 * (q + r / 2);
  const y = HEX * 1.5 * r;
  return flip ? [-x, -y] : [x, y];
}

function cellAt(x, y) {
  if (flip) { x = -x; y = -y; }
  const r = y / (1.5 * HEX);
  const q = x / (HEX * SQ3) - r / 2;
  // arrotondamento in coordinate cubiche
  let rq = Math.round(q), rr = Math.round(r), rs = Math.round(-q - r);
  const dq = Math.abs(rq - q), dr = Math.abs(rr - r), ds = Math.abs(rs + q + r);
  if (dq > dr && dq > ds) rq = -rr - rs;
  else if (dr > ds) rr = -rq - rs;
  return [rq, rr];
}

function corner(cx, cy, size, angleDeg) {
  const a = (Math.PI / 180) * angleDeg;
  return [cx + size * Math.cos(a), cy + size * Math.sin(a)];
}

function hexPoints(cx, cy, size) {
  const pts = [];
  for (let i = 0; i < 6; i++) pts.push(corner(cx, cy, size, 60 * i - 30).join(","));
  return pts.join(" ");
}

function mk(tag, attrs = {}, parent = null) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

const tr = ([x, y]) => `translate(${x}px, ${y}px)`;

function svgPoint(ev) {
  const p = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(svg.getScreenCTM().inverse());
  return [p.x, p.y];
}

// punto un po' più corto del segmento a->b, per non coprire i centri delle celle
function shorten([x1, y1], [x2, y2], a, b) {
  const len = Math.hypot(x2 - x1, y2 - y1) || 1;
  const ux = (x2 - x1) / len, uy = (y2 - y1) / len;
  return [[x1 + ux * a, y1 + uy * a], [x2 - ux * b, y2 - uy * b]];
}

function arrow(parent, from, to, cls, a = HEX * 0.35, b = HEX * 0.45) {
  const [p, q] = shorten(from, to, a, b);
  return mk("line", { x1: p[0], y1: p[1], x2: q[0], y2: q[1], class: `arrow ${cls}` }, parent);
}

// dove disegnare la fine di una pedina spinta fuori dal campo
function offPoint(m) {
  const [x1, y1] = px(m.q, m.r), [x2, y2] = px(...m.to);
  return [x2 + (x2 - x1) * 0.9, y2 + (y2 - y1) * 0.9];
}

function pushedPoint(m) {
  return m.pushed_to === "off" ? offPoint(m) : px(...m.pushed_to);
}

// --- disegno -------------------------------------------------------------------------

function render() {
  const R = S.rules.R;
  const W = HEX * SQ3 * (2 * R + 1) + HEX * 1.6;
  const H = HEX * (3 * R + 2) + HEX * 1.6;
  svg.setAttribute("viewBox", `${-W / 2} ${-H / 2} ${W} ${H}`);
  svg.replaceChildren();
  const defs = mk("defs", {}, svg);
  for (const name of ["A", "B", "push", "ko"]) {
    const m = mk("marker", {
      id: `head-${name}`, viewBox: "0 0 10 10", refX: 6, refY: 5,
      markerWidth: 4, markerHeight: 4, orient: "auto-start-reverse",
    }, defs);
    mk("path", { d: "M0,0 L10,5 L0,10 z", class: `head head-${name}` }, m);
  }
  layers = {};
  for (const name of ["cells", "pieces", "last", "marks", "preview", "drag"]) {
    layers[name] = mk("g", { class: `layer-${name}` }, svg);
  }

  const cells = new Set(S.cells.map(([q, r]) => key(q, r)));
  const home = new Map();
  for (const p of ["A", "B"]) for (const [q, r] of S.home[p]) home.set(key(q, r), p);
  for (const [q, r] of S.cells) {
    const [x, y] = px(q, r);
    const h = home.get(key(q, r));
    mk("polygon", { points: hexPoints(x, y, HEX - 1), class: `cell${h ? " home-" + h : ""}` },
      layers.cells);
  }
  // traguardi: esagono tratteggiato del colore di chi ci deve arrivare
  for (const p of ["A", "B"]) {
    for (const [q, r] of S.goal[p]) {
      const [x, y] = px(q, r);
      mk("polygon", { points: hexPoints(x, y, HEX * 0.78), class: `goal goal-${p}` },
        layers.cells);
    }
  }
  // bordi: le file di casa nel colore del giocatore, gli altri lati sono muri
  for (const [q, r] of S.cells) {
    const [x, y] = px(q, r);
    for (const [dq, dr] of NEIGH) {
      const nq = q + dq, nr = r + dr;
      if (cells.has(key(nq, nr))) continue;
      const [nx, ny] = px(nq, nr);
      const ang = (Math.atan2(ny - y, nx - x) * 180) / Math.PI;
      const [ax, ay] = corner(x, y, HEX, ang - 30), [bx, by] = corner(x, y, HEX, ang + 30);
      const cls = nr < -R ? "edge edge-A" : nr > R ? "edge edge-B" : "edge wall";
      mk("line", { x1: ax, y1: ay, x2: bx, y2: by, class: cls }, layers.cells);
    }
  }

  // ultimo turno dell'avversario
  for (const m of S.last_turn) {
    if (m.pass) continue;
    // sopra le pedine: la punta si ferma sul bordo della pedina arrivata
    arrow(layers.last, px(m.q, m.r), px(...m.to), `last last-${m.player}`, HEX * 0.35, HEX * 0.82);
    if (m.pushed_to) {
      arrow(layers.last, px(...m.to), pushedPoint(m), `last last-${m.player} pushed`,
        HEX * 0.75, HEX * 0.82);
    }
  }

  pieceEls = new Map();
  const myTurn = canAct();
  const locked = S.locked && !S.result ? key(...S.locked) : null;
  for (const [q, r, p] of S.board) {
    const k = key(q, r);
    let cls = "";
    if (k === locked) cls += " locked";
    if (myTurn && (moves.has(k) || koMoves.has(k))) cls += " movable";
    const g = pieceEl(p, px(q, r), cls);
    g.dataset.cell = k;
    layers.pieces.appendChild(g);
    pieceEls.set(k, g);
  }
  drawMarks();
}

function pieceEl(p, pos, cls = "") {
  const g = mk("g", { class: `piece piece-${p}${cls}` });
  g.style.transform = tr(pos);
  mk("circle", { r: HEX * 0.64, class: "body" }, g);
  // forma interna: le pedine si distinguono anche senza colori
  if (p === "A") mk("circle", { r: HEX * 0.3, class: "sign" }, g);
  else {
    const s = HEX * 0.3;
    mk("polygon", { points: `0,${-s} ${s},0 0,${s} ${-s},0`, class: "sign" }, g);
  }
  return g;
}

function drawMarks() {
  if (!layers.marks) return;
  layers.marks.replaceChildren();
  layers.preview.replaceChildren();
  const src = press?.dragging ? press.src : selected;
  if (!src || !canAct()) return;
  const [sq, sr] = src.split(",").map(Number);
  const [sx, sy] = px(sq, sr);
  mk("circle", { cx: sx, cy: sy, r: HEX * 0.78, class: "selected-ring" }, layers.marks);
  const active = press?.dragging ? press.dest : hoverDest;
  for (const m of moves.get(src) || []) {
    const [x, y] = px(...m.to);
    const on = active && active.d === m.d;
    mk("polygon", {
      points: hexPoints(x, y, HEX * 0.86),
      class: `dest${m.push ? " dest-push" : ""}${on ? " active" : ""}`,
    }, layers.marks);
    if (!m.push) mk("circle", { cx: x, cy: y, r: HEX * 0.16, class: "dest-dot" }, layers.marks);
  }
  for (const m of koMoves.get(src) || []) {
    const [x, y] = px(...m.to);
    const g = mk("g", { class: "ko" }, layers.marks);
    mk("title", {}, g).textContent = m.rule === "pushback" ? PUSHBACK_TEXT : KO_TEXT;
    mk("polygon", { points: hexPoints(x, y, HEX * 0.86) }, g);
    const [a, b] = [corner(x, y, HEX * 0.6, 225), corner(x, y, HEX * 0.6, 45)];
    mk("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1] }, g);
    const [c, d] = [corner(x, y, HEX * 0.6, 135), corner(x, y, HEX * 0.6, 315)];
    mk("line", { x1: c[0], y1: c[1], x2: d[0], y2: d[1] }, g);
  }
  // anteprima: dove finisce la pedina spinta
  if (active && active.push) {
    arrow(layers.preview, px(...active.to), pushedPoint(active), "preview", HEX * 0.2, HEX * 0.35);
    if (active.pushed_to === "off") {
      const [x, y] = offPoint(active);
      mk("text", { x, y: y + HEX * 0.25, class: "off-mark" }, layers.preview).textContent = "✕";
    }
  }
}

// --- animazioni ----------------------------------------------------------------------

function animateLast(prev) {
  const m = S.last_move;
  if (!m || m.pass) return;
  const to = key(...m.to);
  const opts = { duration: 260, easing: "cubic-bezier(.2,.7,.3,1)" };
  const mover = pieceEls.get(to);
  const from = pendingDrop && pendingDrop.to === to ? pendingDrop.pos : px(m.q, m.r);
  if (mover) mover.animate([{ transform: tr(from) }, { transform: tr(px(...m.to)) }], opts);
  if (m.pushed_to === "off") {
    const owner = prev.board.find(([q, r]) => key(q, r) === to)?.[2];
    if (!owner) return;
    const ghost = pieceEl(owner, px(...m.to), " ghost");
    layers.pieces.appendChild(ghost);
    ghost.animate([{ transform: tr(px(...m.to)), opacity: 1 },
                   { transform: tr(offPoint(m)), opacity: 0 }], { ...opts, duration: 420 })
      .finished.then(() => ghost.remove(), () => ghost.remove());
  } else if (m.pushed_to) {
    const pushed = pieceEls.get(key(...m.pushed_to));
    if (pushed) {
      pushed.animate([{ transform: tr(px(...m.to)) }, { transform: tr(px(...m.pushed_to)) }],
        { ...opts, delay: 40 });
    }
  }
}

// --- stato ---------------------------------------------------------------------------

function canAct() {
  return S && S.you && S.you === S.to_move && !S.result && !S.thinking && !waiting;
}

function groupBySource(list) {
  const out = new Map();
  for (const m of list) {
    const k = key(m.q, m.r);
    if (!out.has(k)) out.set(k, []);
    out.get(k).push(m);
  }
  return out;
}

function onState(st) {
  const prev = S;
  const samePosition = prev && prev.ply === st.ply && prev.to_move === st.to_move &&
    prev.result === st.result && prev.thinking === st.thinking;
  S = st;
  if (S.bot) playComputer(S.bot);
  if (samePosition && press) {
    // cambia solo chi è collegato: non si interrompe un trascinamento
    updatePanel();
    return;
  }
  waiting = false;
  flip = (S.you || "A") === "A";
  moves = groupBySource(S.legal);
  koMoves = groupBySource(S.ko_forbidden);
  if (!samePosition || !canAct() || !(moves.has(selected) || koMoves.has(selected))) {
    selected = null;
  }
  hoverDest = null;
  press = null;
  render();
  if (prev && S.ply === prev.ply + 1) animateLast(prev);
  pendingDrop = null;
  updatePanel();
}

function sendMove(m) {
  if (!send({ type: "move", q: m.q, r: m.r, d: m.d })) return false;
  waiting = true;
  selected = null;
  hoverDest = null;
  drawMarks();
  return true;
}

// mossa del computer: ricerca nei worker, pausa minima per vederle una alla volta,
// poi al server (che ignora le risposte arrivate quando la partita è già andata avanti)
async function playComputer(req) {
  if (botPly === req.ply) return;
  botPly = req.ply;
  const t0 = performance.now();
  try {
    const { move, info } = await (await computer).choose(req.search,
      { nSim: req.n_sim, timeLimit: req.time_limit, workers: req.workers, policy: req.policy,
        prior: !!req.prior });
    info.ms = Math.round(performance.now() - t0);  // tempo di ricerca, senza la pausa
    const pause = req.delay * 1000 - (performance.now() - t0);
    if (pause > 0) await new Promise((ok) => setTimeout(ok, pause));
    if (!S.bot || S.bot.ply !== req.ply) return;
    send({ type: "bot_move", ply: req.ply, q: move[0], r: move[1], d: move[2], info });
  } catch (e) {
    botPly = null;
    showHint(`The computer couldn't compute its move: ${e.message}`, true);
  }
}

function send(obj) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    showHint("Not connected: trying to reconnect…", true);
    return false;
  }
  ws.send(JSON.stringify(obj));
  return true;
}

// --- pannello ------------------------------------------------------------------------

function swatch(p) {
  return `<svg class="mini" viewBox="-20 -20 40 40" aria-hidden="true">` +
    `<g class="piece piece-${p}"><circle r="17" class="body"/>` +
    (p === "A" ? `<circle r="8" class="sign"/>` : `<polygon points="0,-8 8,0 0,8 -8,0" class="sign"/>`) +
    `</g></svg>`;
}

function updatePanel() {
  const you = S.you;
  $("players").innerHTML = ["A", "B"].map((p) => {
    const pl = S.players[p];
    const tag = p === you ? " <strong>(you)</strong>" : "";
    const on = pl.online ? "online" : "offline";
    const turn = !S.result && S.to_move === p ? " turn" : "";
    const label = pl.kind === "free" ? "free seat" : escapeHtml(pl.name);
    return `<div class="player${turn}">${swatch(p)}<span>${NAMES[p]}${tag}</span>` +
      `<span class="who">${label}</span><span class="dot ${on}" title="${on}"></span></div>`;
  }).join("");

  let status = "";
  const mine = !S.result && you === S.to_move;
  if (S.result) status = resultLine();
  else if (S.thinking) status = "The computer is thinking…";
  else if (mine) {
    if (S.locked) status = "Your turn: second move, with another piece.";
    else if (S.ply === 0 && S.rules.moves_per_turn === 2) status = "Your turn: opening move (just one).";
    else if (S.moves_left === 2) status = "Your turn: two moves.";
    else status = "Your turn.";
  } else if (you) {
    const other = you === "A" ? "B" : "A";
    status = S.players[other].kind === "free" ? "Waiting for your opponent…" : "Your opponent's turn.";
  } else status = `${NAMES[S.to_move]} to move. You are watching this game.`;
  // barra sopra il campo: piena nel proprio colore quando tocca a chi guarda
  const cls = S.result ? "ended" : mine ? `mine turn-${you}${S.locked ? " second" : ""}` : "";
  setBar(status, cls, S.result ? null : S.to_move);

  const res = $("result");
  if (S.result) {
    const why = reasonText();
    res.innerHTML = `<strong>${resultText()}</strong>${why ? ` — ${why}` : ""}.` +
      (S.rated ? ` Your rating: ${"★".repeat(S.rated)}${"☆".repeat(5 - S.rated)}.` : "") +
      ` <a href="/">New game</a>`;
    res.hidden = false;
  } else res.hidden = true;
  updateRating();

  $("undo").disabled = !S.can_undo || waiting;
  $("undo").hidden = !you || S.rules.moves_per_turn < 2 || !!S.result;
  $("resign").hidden = !you || !!S.result;
  const share = $("share");
  if (share) share.hidden = !Object.values(S.players).some((p) => p.kind === "free");

  const r = S.rules;
  const parts = [`R=${r.R}`, `${r.N} pieces`, r.K > 1 ? `K=${r.K}` : null,
    r.goal_wide ? "wide goal" : null, r.push_off ? "push off" : null,
    r.no_pushback ? "no push-back" : null, r.ko ? "ko" : null,
    `${r.moves_per_turn} move${r.moves_per_turn > 1 ? "s" : ""} per turn`].filter(Boolean);
  let extra = "";
  if (r.K > 1) extra += ` On the goal: ${NAMES.A} ${S.reached.A}/${r.K}, ${NAMES.B} ${S.reached.B}/${r.K}.`;
  if (r.push_off) extra += ` Pieces: ${NAMES.A} ${S.pieces.A}, ${NAMES.B} ${S.pieces.B}.`;
  $("rules-summary").textContent = `Rules: ${parts.join(", ")}. Ply ${S.ply}/${r.max_plies}.${extra}`;
}

function setBar(text, cls = "", player = null) {
  const bar = $("status");
  bar.className = `turn-bar ${cls}`.trim();
  bar.innerHTML = (player ? swatch(player) : "") + `<span>${escapeHtml(text)}</span>`;
}

function resultText() {
  const you = S.you;
  if (S.result === "draw") return "Draw";
  if (!you) return `${NAMES[S.result]} wins`;
  if (S.resigned === you) return "You resigned";
  return S.result === you ? "You won!" : "You lost";
}

// motivo della fine, detto dal punto di vista di chi guarda
function reasonText() {
  if (S.reason !== "resign") return REASONS[S.reason] || S.reason;
  if (S.you === S.resigned) return null;
  return S.you ? "your opponent resigned" : `${NAMES[S.resigned]} resigned`;
}

function resultLine() {
  const why = reasonText();
  return why ? `${resultText()} — ${why}` : resultText();
}

function escapeHtml(text) {
  const d = document.createElement("div");
  d.textContent = text ?? "";
  return d.innerHTML;
}

// --- giudizio sul divertimento -----------------------------------------------------

function updateRating() {
  const canRate = !!(S.result && S.you);
  const btn = $("rate-open");
  btn.hidden = !canRate;
  btn.textContent = S.rated ? "Change your rating" : "Rate the game";
  if (canRate && !S.rated && !ratingShown) {
    ratingShown = true;
    setTimeout(openRating, 900);  // dopo l'animazione dell'ultima mossa
  }
}

function openRating() {
  const dlg = $("rating");
  if (dlg.open) return;
  $("rating-title").textContent = resultLine();
  setStars(S.rated || 0);
  dlg.showModal();
}

function setStars(n) {
  stars = n;
  document.querySelectorAll("#rating .star").forEach((b) => {
    const on = Number(b.dataset.stars) <= n;
    b.classList.toggle("on", on);
    b.setAttribute("aria-checked", String(Number(b.dataset.stars) === n));
  });
  const words = ["Choose from 1 to 5 stars.", "Not at all", "A little", "Quite a lot",
    "A lot", "Loads"];
  $("stars-label").textContent = words[n];
  $("rating-send").disabled = n === 0;
}

document.querySelectorAll("#rating .star").forEach((b) => {
  b.addEventListener("click", () => setStars(Number(b.dataset.stars)));
});
$("rating-form").addEventListener("submit", (ev) => {
  if (!stars) { ev.preventDefault(); return; }
  if (send({ type: "rate", stars, comment: $("rating-comment").value.trim() })) {
    showHint("Thanks for rating!");
  }
});
$("rating-skip").addEventListener("click", () => $("rating").close());
$("rate-open").addEventListener("click", openRating);

function showHint(text, isError = false) {
  const h = $("hint");
  h.textContent = text;
  h.classList.toggle("error", isError);
  clearTimeout(hintTimer);
  hintTimer = setTimeout(() => { h.textContent = ""; }, 5000);
}

// --- input: clic-clic e trascinamento (Pointer Events: mouse, penna, tocco) -----------

function moveTo(src, cellKey) {
  return (moves.get(src) || []).find((m) => key(...m.to) === cellKey) || null;
}

function koTo(src, cellKey) {
  return (koMoves.get(src) || []).find((m) => key(...m.to) === cellKey) || null;
}

function nearestDest(src, x, y) {
  let best = null, bestD = HEX * 1.05;
  for (const m of moves.get(src) || []) {
    const [cx, cy] = px(...m.to);
    const d = Math.hypot(cx - x, cy - y);
    if (d < bestD) { best = m; bestD = d; }
  }
  return best;
}

svg.addEventListener("pointerdown", (ev) => {
  if (!S || !canAct() || ev.button > 0 || press) return;
  const [x, y] = svgPoint(ev);
  const k = key(...cellAt(x, y));
  const dest = selected ? moveTo(selected, k) : null;
  if (moves.has(k) || koMoves.has(k)) {
    // pedina propria: clic (selezione, o destinazione se è una spinta della selezionata)
    // oppure inizio di un trascinamento
    press = { src: k, id: ev.pointerId, start: [x, y], dragging: false, dest: null, clickMove: dest };
    svg.setPointerCapture(ev.pointerId);
    ev.preventDefault();
    return;
  }
  if (dest) {
    sendMove(dest);
    return;
  }
  const banned = selected && koTo(selected, k);
  if (banned) {
    showHint(banned.rule === "pushback" ? PUSHBACK_TEXT : KO_TEXT);
    return;
  }
  selected = null;
  hoverDest = null;
  drawMarks();
});

svg.addEventListener("pointermove", (ev) => {
  if (!S) return;
  const [x, y] = svgPoint(ev);
  if (press && ev.pointerId === press.id) {
    if (!press.dragging && Math.hypot(x - press.start[0], y - press.start[1]) > HEX * 0.3) {
      if (!moves.has(press.src)) return; // solo mosse vietate: niente da trascinare
      press.dragging = true;
      selected = press.src;
      const el = pieceEls.get(press.src);
      layers.drag.appendChild(el);
      el.classList.add("dragging");
    }
    if (press.dragging) {
      pieceEls.get(press.src).style.transform = tr([x, y]);
      const dest = nearestDest(press.src, x, y);
      if (dest !== press.dest) {
        press.dest = dest;
        drawMarks();
      }
    }
    return;
  }
  if (!selected || !canAct()) return;
  const k = key(...cellAt(x, y));
  const dest = moveTo(selected, k);
  if (dest !== hoverDest) {
    hoverDest = dest;
    drawMarks();
  }
  svg.classList.toggle("over-ko", !!koTo(selected, k));
});

function endPress(ev, cancelled) {
  if (!press || ev.pointerId !== press.id) return;
  const p = press;
  press = null;
  if (p.dragging) {
    const el = pieceEls.get(p.src);
    if (!cancelled && p.dest) {
      pendingDrop = { to: key(...p.dest.to), pos: svgPoint(ev) };
      if (sendMove(p.dest)) return;
    }
    // rilascio fuori da una destinazione legale: la pedina torna al suo posto
    const [q, r] = p.src.split(",").map(Number);
    const home = tr(px(q, r));
    const cur = el.style.transform;
    el.style.transform = home;
    el.classList.remove("dragging");
    layers.pieces.appendChild(el);
    el.animate([{ transform: cur }, { transform: home }], { duration: 180, easing: "ease-out" });
    selected = p.src;
    drawMarks();
    return;
  }
  if (cancelled) return;
  if (p.clickMove) sendMove(p.clickMove);
  else {
    selected = selected === p.src ? null : p.src;
    hoverDest = null;
    if (selected && !moves.has(selected)) showHint("This piece only has forbidden moves (ko or push-back).");
    drawMarks();
  }
}

svg.addEventListener("pointerup", (ev) => endPress(ev, false));
svg.addEventListener("pointercancel", (ev) => endPress(ev, true));
svg.addEventListener("pointerleave", () => {
  if (!press && hoverDest) { hoverDest = null; drawMarks(); }
});

document.addEventListener("keydown", (ev) => {
  if (ev.key === "Escape" && selected) { selected = null; hoverDest = null; drawMarks(); }
});

$("undo").addEventListener("click", () => {
  if (send({ type: "undo" })) waiting = true;
});
$("resign").addEventListener("click", () => {
  if (confirm("Do you really want to resign?")) send({ type: "resign" });
});
$("copy")?.addEventListener("click", async () => {
  const input = $("share-url");
  try {
    await navigator.clipboard.writeText(input.value);
    showHint("Link copied.");
  } catch {
    input.select();
  }
});

// --- connessione ---------------------------------------------------------------------

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/game/${matchId}/`);
  ws.onopen = () => {
    retry = 0;
    botPly = null;  // una risposta del computer può essersi persa con la connessione
  };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "state") onState(msg);
    else if (msg.type === "error") showHint(msg.message, true);
  };
  ws.onclose = (ev) => {
    if (ev.code === 4404) {
      setBar("No such game: maybe the server was restarted.");
      return;
    }
    setBar("Connection lost, reconnecting…");
    setTimeout(connect, Math.min(10000, 1000 * 2 ** retry++));
  };
}

connect();
