// Web Worker del computer: una ricerca MCTS (mcts.js) alla volta, fuori dal thread della
// pagina. Il motore si importa con la stessa versione dei file statici (?v=...).

const engine = import(`./mcts.js${new URL(import.meta.url).search}`);

self.onmessage = async (ev) => {
  const { id, state, nSim, timeLimit, seed, policy, prior = false } = ev.data;
  try {
    const { search } = await engine;
    self.postMessage({ id, result: search(state, { nSim, timeLimit, seed, policy, prior }) });
  } catch (e) {
    self.postMessage({ id, error: String(e && e.stack || e) });
  }
};
