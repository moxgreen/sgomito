// Motore del browser (web/play/static/play/mcts.js) al servizio di Python: una richiesta
// JSON per riga su stdin, una risposta JSON per riga su stdout. Lo usa hexpush/jsbot.py.
//
//   {"op": "search", "state": ..., "opts": {...}}  -> risultato di search(), più "ms"
//   {"op": "features", "state": ...}              -> {"moves": [[q, r, d], ...],
//                                                    "features": [[...], ...]}
//   {"op": "names"}                               -> {"names": FEATURE_NAMES}
// Lo stato è serialize.search_state_to_dict.

import { createInterface } from "node:readline";
import { FEATURE_NAMES, FastBoard, moveFeatures, search } from "../web/play/static/play/mcts.js";

function features(state) {
  const fb = new FastBoard(state);
  const moves = fb.moves();
  return {
    moves: moves.map(([src, d]) => [...fb.geo.cells[src], d]),
    features: moveFeatures(fb, moves).map((f) => Array.from(f)),
  };
}

const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of rl) {
  if (!line.trim()) continue;
  const req = JSON.parse(line);
  let out;
  try {
    if (req.op === "search") {
      const t0 = performance.now();
      out = search(req.state, req.opts || {});
      out.ms = performance.now() - t0;
    } else if (req.op === "features") out = features(req.state);
    else if (req.op === "names") out = { names: FEATURE_NAMES };
    else out = { error: `operazione sconosciuta: ${req.op}` };
  } catch (e) {
    out = { error: String((e && e.stack) || e) };
  }
  process.stdout.write(JSON.stringify(out) + "\n");
}
