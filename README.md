# Spinta — gioco di spinta su esagoni

Gioco a due su un campo esagonale: si vince portando una propria pedina sulla fila di casa
dell'avversario. A ogni turno si muovono due pedine diverse, e si può spingere una pedina
(propria o avversaria) se dietro c'è una cella libera. Progetto didattico: le regole sono
state messe alla prova con bot che giocano tra loro prima di arrivare agli umani.

- `hexpush/`: motore in Python puro, bot (Random, Greedy, patologici, MCTS) e self-play.
- `web/`: il sito (Django + Channels), per giocare con un amico o contro il computer, che
  calcola nel browser. Vedi [web/README.md](web/README.md).
- `docs/` e `results/`: protocolli di misura e risultati. Regole, decisioni e numeri sono
  riassunti in [CLAUDE.md](CLAUDE.md).

## Articoli

- [Measuring Depth by Doubling](https://claude.ai/artifact/GVAUBRCEUCGpa84nNeSt4E)
  (in inglese): bot e profondità statistica del gioco, la curva a raddoppi.
  Copia in `docs/articles/bots.html`.
- [Does Depth Bring Variety?](https://claude.ai/artifact/XyECex874wGPwE3hBk5ssx)
  (in inglese): profondità e varietà delle partite, aperture, stili e finali.
  Copia in `docs/articles/variety.html`.
- [Bot Monte Carlo (MCTS) nel gioco di spinta](https://claude.ai/artifact/JKGgALFcP9ohSC6CQ1SRxk)
  (in italiano): come funziona il bot MCTS.

In preparazione: "Does a bigger board make a more varied game?" sul campo con R=5
(bozza in `docs/articles/src/bigger.tmpl.html`).

## Comandi

```bash
make venv && make migrate   # una volta: ambiente e database
make run                    # sito su http://127.0.0.1:8000/
make test                   # tutti i test (motore + web)
make ladder                 # curva a raddoppi MCTS (serve pypy3)
make help                   # tutti gli altri
```
