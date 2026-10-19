# Test della curva a raddoppi (MCTS)

Integra la sezione "Livello 3 — MCTS" di `CLAUDE.md`. Dove i due documenti
divergono, vale questo.

## Domanda

Il gioco continua a premiare il calcolo anche quando i bot sono già buoni?
Un gioco con strategia mostra un guadagno regolare a ogni raddoppio delle
simulazioni. Un gioco superficiale mostra un guadagno grande all'inizio e poi si
appiattisce verso il 50%.

Il confronto singolo `MCTS(2000)` contro `MCTS(100)` (96% con `ko`) **non basta**:
100 simulazioni sono circa 3 playout per mossa, quindi MCTS(100) è quasi un bot
casuale e quasi qualunque gioco darebbe un risultato simile. La soglia "80%" del
`CLAUDE.md` è una condizione necessaria, non il test.

## Protocollo

1. Livelli di simulazioni: `100, 200, 400, 800, 1600, 3200`.
2. Incontri: ogni livello contro quello col doppio (100 vs 200, 200 vs 400, ...,
   1600 vs 3200). Cinque incontri.
3. Stesso `Ruleset` per tutti, con `ko` attivo anche dentro i playout.
4. Colori alternati: metà delle partite col livello forte come A, metà come B.
   Seed fissi e salvati nel CSV.
5. Partite per incontro: almeno 200 (errore ~±7 punti percentuali vicino al 50%).
   Se i livelli alti sono troppo lenti, scendere a 100 e dirlo nel report.
6. Per ogni incontro riportare **separatamente**: vittorie, sconfitte, patte del
   livello forte, e la percentuale con le patte contate 0.5.
7. Calcolare la differenza Elo: `elo = 400 * log10(p / (1 - p))`, con intervallo di
   confidenza al 95% (`p ± 1.96 * sqrt(p * (1 - p) / n)`, poi convertito in Elo).
8. Salvare tutto in un CSV (`livello_forte, livello_debole, colore_forte, esito,
   semimosse, n_spinte, seed`) e stampare la tabella riassuntiva.

## Controlli aggiuntivi (stessa tabella)

- `MCTS(100)` contro `RandomBot`: dice quanto il 96% di prima era "distanza dal
  caso". Atteso: molto alto se anche 100 simulazioni sono già sensate.
- `MCTS(2000)` contro `GreedyBot`: se vince nettamente, il guadagno viene dalla
  ricerca e non dall'euristica (i playout troncati usano la stessa euristica).
- `MCTS(2000)` contro `TurtleBot`, `RusherBot`, `PingPongBot`: i patologici devono
  perdere (soglia nel `CLAUDE.md`: almeno 70% di vittorie del Greedy).

## Come leggere la curva

| incontro        | c'è profondità                   | il gioco è superficiale        |
|-----------------|----------------------------------|--------------------------------|
| 100 vs 200      | 60–70% al più forte              | 70% o più                      |
| 400 vs 800      | ancora 60–65%                    | 55–60%                         |
| 1600 vs 3200    | ancora 55–60%                    | ~50%                           |

Regola pratica: guardare gli Elo per raddoppio. Una riga piatta (per esempio
100–150 Elo a ogni raddoppio) indica profondità. Una curva che scende verso zero
indica che il gioco si "risolve" e che gli esiti dipendono dal rumore dei playout.

Rispetto ai 96%: 100 → 2000 sono circa 4.3 raddoppi e ~550 Elo, cioè ~130 Elo per
raddoppio in media. Il test serve a capire se questo guadagno resta costante.

## Decisione

- Curva che resta sopra ~55% fino a 1600 vs 3200, patologici che perdono,
  MCTS batte Greedy: il gioco ha strategia, si può passare agli umani e a Django.
- Curva che si appiattisce sotto 55% già verso 400 vs 800: il gioco è troppo
  superficiale. Provare in ordine `goal_wide`, `K=2`, regola di maggioranza,
  stordimento della pedina spinta, e ripetere il protocollo.
- Patte oltre il 10%: il ping-pong si è spostato su cicli più lunghi di quelli
  coperti da `ko`. Verificare con `PingPongBot` e aggiungere la regola di tripla
  ripetizione ai playout.

## Note di implementazione

- Usare PyPy (`pypy3`) per il self-play; `sweep.py` e le tabelle con pandas girano
  con CPython.
- Parallelizzare le partite con `multiprocessing`: sono indipendenti.
- Stima dei tempi: a 3200 simulazioni in CPython siamo intorno ai 6 s a mossa, quindi
  parecchi minuti a partita. Con PyPy e più processi diventa gestibile, ma conviene
  far girare prima 20 partite per misurare il tempo reale.
- Per ogni mossa MCTS registrare anche le visite dei figli: se due mosse hanno quote
  di visite molto vicine (40% e 38%), il bot non sa decidere, ed è un dato sul gioco.

Implementato in `hexpush/ladder.py`.
