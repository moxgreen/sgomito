// Velocità della ricerca del browser (mcts.js) in node, un solo albero.
// Lo stato (serialize.search_state_to_dict) arriva su stdin: vedi `make bench-js`.
//
//   node tests/js/bench.mjs [simulazioni] [prior]
// Con "prior" la ricerca è guidata dalla policy (mcts.search, opzione prior).

import { search } from "../../web/play/static/play/mcts.js";

const nSim = Number(process.argv[2] || 20000);
const prior = process.argv[3] === "prior";
let input = "";
for await (const chunk of process.stdin) input += chunk;
const state = JSON.parse(input);
search(state, { nSim: 2000, seed: 1, prior });  // riscaldamento del JIT
const t0 = performance.now();
const res = search(state, { nSim, seed: 2, prior });
const secs = (performance.now() - t0) / 1000;
console.log(`${res.sims} simulazioni in ${secs.toFixed(2)} s: ${Math.round(res.sims / secs)} al secondo`);
