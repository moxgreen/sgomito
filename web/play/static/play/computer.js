// Il computer gioca nel browser: una o più ricerche MCTS (mcts.js) in Web Worker, così la
// pagina resta fluida. Con più worker è la parallelizzazione alla radice di
// hexpush/parallel.py: alberi indipendenti con semi diversi, visite sommate mossa per mossa.

const VERSION = new URL(import.meta.url).search;  // stessa versione dei file statici

/** Come parallel.default_workers: metà dei core logici, al massimo 8. */
export function defaultWorkers() {
  const cores = (typeof navigator !== "undefined" && navigator.hardwareConcurrency) || 2;
  return Math.max(1, Math.min(Math.floor(cores / 2), 8));
}

/** Mossa scelta dai risultati di search() di uno o più worker: una mossa obbligata o
 * vincente se c'è, altrimenti la più visitata in totale; a parità di visite conta il
 * valore, poi l'ordine d'arrivo. */
export function combine(results) {
  const moves = new Map(), visits = new Map(), values = new Map();
  for (const res of results) {
    if (res.move !== undefined) return res.move;
    for (const [m, n, v] of res.children) {
      const k = m === null ? "pass" : m.join(",");
      if (!moves.has(k)) moves.set(k, m);
      visits.set(k, (visits.get(k) || 0) + n);
      values.set(k, (values.get(k) || 0) + v);
    }
  }
  let best = null;
  for (const k of moves.keys()) {
    if (best === null || visits.get(k) > visits.get(best) ||
        (visits.get(k) === visits.get(best) && values.get(k) > values.get(best))) best = k;
  }
  return moves.get(best);
}

export class Computer {
  constructor() {
    this.workers = [];        // creati alla prima mossa e poi riusati
    this.pending = new Map(); // id della richiesta -> {resolve, reject, worker}
    this.nextId = 0;
  }

  worker(i) {
    if (!this.workers[i]) {
      const w = new Worker(new URL(`./bot-worker.js${VERSION}`, import.meta.url), { type: "module" });
      w.onmessage = (ev) => {
        const p = this.pending.get(ev.data.id);
        if (!p) return;
        this.pending.delete(ev.data.id);
        if (ev.data.error) p.reject(new Error(ev.data.error));
        else p.resolve(ev.data.result);
      };
      w.onerror = (ev) => {
        // worker rotto: si scarta, alla prossima mossa se ne crea uno nuovo
        this.workers[i] = null;
        for (const [id, p] of this.pending) {
          if (p.worker === w) {
            this.pending.delete(id);
            p.reject(new Error(ev.message || "worker error"));
          }
        }
      };
      this.workers[i] = w;
    }
    return this.workers[i];
  }

  run(i, msg) {
    const w = this.worker(i);
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject, worker: w });
      w.postMessage({ id, ...msg });
    });
  }

  /** {move, info} per lo stato `state` (serialize.search_state_to_dict): move è
   * [q, r, d], info le statistiche della ricerca per il registro (worker, simulazioni
   * nuove ed ereditate dall'albero precedente, mossa obbligata o vincente).
   * nSim simulazioni in totale, divise tra i worker; timeLimit secondi (o null),
   * workers un numero o "auto",
   * policy la gestione del tempo (mcts.policyStop) o null: ogni worker la applica al
   * proprio albero. */
  async choose(state, { nSim, timeLimit = null, workers = 1, policy = null }) {
    const n = workers === "auto" ? defaultWorkers() : workers;
    const seeds = crypto.getRandomValues(new Uint32Array(n));
    const each = Math.ceil(nSim / n);
    const results = await Promise.all(
      Array.from(seeds, (seed, i) => this.run(i, { state, nSim: each, timeLimit, seed, policy })));
    const info = { workers: n, forced: results.some((res) => res.move !== undefined) };
    if (!info.forced) {
      info.sims = results.reduce((s, res) => s + res.sims, 0);
      info.reused = results.reduce((s, res) => s + res.reused, 0);
    }
    return { move: combine(results), info };
  }
}
