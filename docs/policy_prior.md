# Policy appresa come prior della ricerca (PUCT)

Obiettivo: la forza del livello Campione (192000 simulazioni su 4 alberi) con meno
calcolo, perché giri bene anche sui telefoni. Un libro d'aperture a posizioni esatte non
serve: dopo 4 semimosse ci sono già 5544 posizioni e le partite tra bot sono tutte
diverse. Serve invece una conoscenza che generalizzi: una policy su feature locali delle
mosse, allenata a imitare una ricerca forte e usata per decidere quali mosse esplorare.

Il bot puro (`mctsd`, e `search()` di `mcts.js` senza opzioni) non cambia: serve alla
curva a raddoppi. Il prior è l'opzione `prior` di `search()`, spenta di default (anche
in `computer.js` e `bot-worker.js`).

## Metodo

1. **Corpus** (`python -m hexpush.policy corpus`): partite di `mcts.js` contro se stesso
   a 20000 simulazioni, con le regole del sito (`ko`, `no_pushback`, due mosse per
   turno). La partita la gioca `game.py`; a ogni mossa lo stato va a un processo node
   (`hexpush/jsserver.mjs`, `hexpush/jsbot.py`), che fa la ricerca. Per ogni posizione si
   salvano le visite di tutti i figli della radice. Le prime 6 semimosse si scelgono a
   caso pesando con le visite, per variare le aperture.
   `results/policy_corpus_js20000.jsonl`: 100 partite, 4979 posizioni con una ricerca vera
   (fermato a 100 su 240: vedi la curva di apprendimento sotto; il comando riprende da
   dove si è fermato).
   Seconda versione (`corpus --prior`): il maestro è la ricerca guidata dalla policy della
   prima versione, sempre a 20000 simulazioni (un giro di expert iteration):
   `results/policy_corpus_jsp20000.jsonl`, 100 partite, 5589 posizioni.
2. **Feature** (`mcts.moveFeatures`, 38 per mossa, dal punto di vista di chi muove):
   direzione (avanti / di lato / indietro), distanza dal traguardo della cella di partenza
   e d'arrivo (una per fila), spinta di una propria pedina o di una avversaria, direzione
   in cui va la pedina avversaria spinta e quanto era vicina al suo traguardo, pedine
   vicine alla cella d'arrivo, quante pedine nemiche possono spingere la pedina prima e
   dopo, se la mossa porta una pedina avversaria sul traguardo, e le minacce dopo la
   mossa: con la mossa che chiude il turno, se l'avversario ha una vittoria immediata e
   quante ne ho io; con la prima mossa del turno, se la seconda può vincere e se
   l'avversario ha una vittoria immediata. La legalità delle minacce è semplificata
   (senza `ko` né `no_pushback`): `tests/test_js_engine.py` le confronta con `game.py`
   dove le due coincidono.
   Le feature esistono solo in JavaScript: `python -m hexpush.policy features` le chiede a
   node per ogni posizione del corpus, così allenamento e sito usano lo stesso codice.
3. **Allenamento** (`python -m hexpush.policy train --install`): regressione logistica
   multinomiale sulle mosse legali (softmax di pesi · feature), perdita = entropia
   incrociata con la distribuzione delle visite, Adam a lotto intero, solo stdlib. 80% delle
   partite per l'allenamento, 20% per la prova. `--install` scrive i pesi in
   `PRIOR_WEIGHTS` di `mcts.js`; i pesi sono anche in `results/policy_weights.json` (prima
   versione) e `results/policy_weights_jsp20000.json` (seconda, quella installata).
   Con `--pairs` il modello ha anche un peso per ogni coppia di feature che compaiono
   insieme in una mossa (il punteggio è Σ wₖ fₖ + Σ wₐᵦ fₐ f_b): resta una regressione
   logistica, sulle feature e sui loro prodotti. Nella seconda versione 38 pesi lineari e
   447 coppie.
4. **Ricerca guidata** (`search(s, {prior: true})`): PUCT al posto di UCT. Un nodo,
   quando si apre, crea subito tutti i figli con la probabilità della policy; si scende
   nel figlio con il massimo di `q + cPuct · prior · sqrt(N + 1) / (1 + n)`, dove i figli
   mai visitati valgono il valore del nodo meno `fpu`. Il primo figlio mai visitato che
   si incontra è l'espansione, poi il playout casuale con mosse decisive come prima.
   Default: `cPuct = 2`, `fpu = 0`, `priorTemp = 1`.
5. **Misura** (`python -m hexpush.policy match`): guidato a N contro puro a N·k, colori
   alternati, Elo con IC al 95% come in `docs/curva_raddoppi.md`. Il costo per
   simulazione si misura nelle stesse partite (stessa macchina, stesso carico).
   Con `--base-js` l'avversario è invece il prior di un'altra copia di `mcts.js` (per
   esempio `git show 5e377a8:web/play/static/play/mcts.js > base.mjs`) alle stesse
   simulazioni: così si confrontano due policy, ognuna col proprio codice delle feature e
   quindi col proprio costo.

## Policy (prima versione)

Curva di apprendimento (perdita di prova; uniforme 2.88-2.90): 16 partite 2.555,
23 partite di allenamento 2.490, 72 partite 2.492, 80 partite 2.469 (altra divisione).
Il modello è saturo già con poche decine di partite: per migliorare servono feature
più ricche, non più dati.

Pesi finali (`results/policy_train_log.txt`): perdita di prova 2.469 (uniforme 2.881);
la mossa più probabile per la policy è la più visitata dalla ricerca a 20000 simulazioni
nel 42% delle posizioni (a caso: 6%). I pesi più grandi sono quelli tattici: lasciare
all'avversario una vittoria immediata (−2.7 a metà turno, −2.5 a fine turno), poter
vincere con la seconda mossa (+1.9); poi la fila d'arrivo e le spinte di pedine
avversarie vicine al loro traguardo.

Taratura di `cPuct` (pesi provvisori da 16 partite, guidato 500, 40 partite per riga):

| cPuct | contro puro 500 | contro puro 2000 |
|-------|-----------------|------------------|
| 0.5   | 82.5%           | 60.0%            |
| 1     | 82.5%           | 72.5%            |
| 2     | 95.0%           | 70.0%            |
| 4     | 87.5%           | 72.5%            |

Con 40 partite l'errore è ±15 punti: tra 1 e 4 non si distingue; 0.5 è peggio.

## Risultati (prima versione)

Guidato a N contro puro a N·k, 100 partite per incontro, colori alternati, pesi di
`mcts.js`, `cPuct = 2` (`results/policy_match_500_*`, `results/policy_match_2000_*`).
Patte: nessuna.

| guidato | puro  | punti del guidato | Elo  | IC 95%      | costo/sim |
|---------|-------|-------------------|------|-------------|-----------|
| 500     | 500   | 91.0%             | +402 | +307..+582  | 1.62      |
| 500     | 1000  | 85.0%             | +301 | +220..+424  | 1.32      |
| 500     | 2000  | 78.0%             | +220 | +146..+317  | 1.31      |
| 500     | 4000  | 63.0%             | +92  | +25..+168   | 1.32      |
| 500     | 8000  | 36.0%             | −100 | −176..−32   | 1.23      |
| 2000    | 2000  | 94.0%             | +478 | +369..+746  | 1.48      |
| 2000    | 4000  | 83.0%             | +275 | +197..+389  | 1.47      |
| 2000    | 8000  | 73.0%             | +173 | +102..+260  | 1.48      |
| 2000    | 16000 | 55.0%             | +35  | −33..+106   | 1.41      |
| 2000    | 32000 | 51.0%             | +7   | −62..+76    | —         |

**Il prior vale circa 2.5 raddoppi a 500 simulazioni e 4–5 raddoppi a 2000.**
Il pareggio (interpolando l'Elo in log2 delle simulazioni del puro) cade a ~5600
simulazioni pure per il guidato a 500 (×11, 2.5 raddoppi) e oltre 32000 per il guidato
a 2000 (≥ ×16 con buona sicurezza: 55% e 51% contro 16000 e 32000; stima puntuale ~5
raddoppi, ma l'IC a 32000 va da −62 a +76 Elo). Il guadagno cresce con N: la ricerca
pura spreca più simulazioni ad allargare ogni nodo a tutte le ~25-30 mosse, il PUCT
le concentra.

**Costo**: una simulazione guidata costa ~1.4 volte una pura (mediana per partita del
rapporto: 1.34 nella serie a 500, 1.42 in quella a 2000), cioè ~0.5 raddoppi. Al netto:
a parità di tempo il guidato vale ~2 raddoppi a 500 e ~4 a 2000 simulazioni guidate.
Il costo è il calcolo delle feature di tutte le mosse quando un nodo si apre (~85 µs
per ~25 mosse, contro ~250 µs di un playout).
Tempi misurati sulla stessa macchina nelle stesse partite, ma con il Mac carico da altri
esperimenti e a tratti sospeso: per questo si usano le mediane per partita; nella riga
contro 32000 anche le mediane sono falsate dalla sospensione e il costo non si riporta.

Per il sito: il livello Campione è ora 192000 simulazioni pure su 4 alberi (la stima
di 4 × 13.5k da cui partiva questo lavoro era misurata con node da terminale su una
macchina carica: nel browser il vecchio Campione a 3 s ne faceva ~185000). 4 alberi da
48000 valgono circa un albero da ~140000, cioè ~3 raddoppi sopra 16-32k: con 2000
simulazioni guidate non ci si arriva. Andando avanti col guadagno che cresce con N,
dovrebbero bastare qualche decina di migliaia di simulazioni guidate su un albero solo.
Da verificare direttamente: guidato (con riuso dell'albero) contro `computer.js` a 4
alberi da 48000, e a tempo fisso nel browser.

## Seconda versione (26 ottobre 2026): coppie di feature e maestro guidato

Punto di partenza: la curva di apprendimento piatta diceva che più partite dallo stesso
maestro non servono. Due strade, ognuna misurata contro il prior della prima versione
(`match --base-js`, copia di `mcts.js` del commit `5e377a8`) alle stesse simulazioni,
200 partite per riga a colori alternati, `cPuct = 2`. Partite in `results/policy_v2/`,
pesi delle varianti in `results/policy_v2/*_pesi.json`.

1. **Feature più ricche**: 10 feature nuove in `moveFeatures` (patch in
   `results/policy_v2/feature_ricche.patch`), tutte calcolate sul campo dopo la mossa:
   cammini liberi verso il traguardo delle pedine a distanza 2 e 3 (dell'avversario) e 2
   (mie); quante pedine avversarie vicine al traguardo hanno la cella d'arrivo davanti;
   distanza 1 o 2 dalla pedina avversaria più avanzata; muovo la mia pedina più avanzata
   (e in avanti); a metà turno, l'avversario ha almeno due vittorie immediate; a metà
   turno, nessuna seconda mossa para le vittorie immediate dell'avversario (lettura
   esatta a 1.5 semimosse, legalità semplificata come le altre minacce). Venivano dagli
   errori più grossi della policy: posizioni in cui la ricerca mette più del 40% delle
   visite su una mossa che la policy mette sesta o oltre, quasi sempre difese (riempire
   la propria fila di casa davanti a una pedina avversaria a distanza 2, far scorrere il
   muro con due mosse).
   Poi le **coppie di feature** (`train --pairs`): con lo stesso corpus la perdita scende
   più con le coppie che con le feature nuove.
2. **Maestro più forte**: corpus giocato dalla ricerca guidata dalla prima policy a
   20000 simulazioni (`corpus --prior`), stesse 38 feature.

Perdita di prova (20% delle partite; l'uniforme è 2.88 sul primo corpus e 2.94 sul
secondo, che quindi non si confrontano direttamente):

| corpus (maestro)   | feature   | coppie | perdita | accordo | prob. della più visitata |
|--------------------|-----------|--------|---------|---------|--------------------------|
| puro 20000         | 38        | —      | 2.469   | 42.3%   | 0.172                    |
| puro 20000         | 48        | —      | 2.428   | 45.9%   | 0.191                    |
| puro 20000         | 38        | 445    | 2.395   | 43.4%   | 0.199                    |
| puro 20000         | 48        | 826    | 2.318   | 47.9%   | 0.238                    |
| guidato 20000      | 38        | —      | 2.215   | 45.0%   | 0.228                    |
| guidato 20000      | 38        | 447    | 2.142   | 48.2%   | 0.253                    |
| guidato 20000      | 48        | 828    | 2.048   | 49.6%   | 0.284                    |

Partite contro il prior della prima versione (stesse simulazioni; costo = µs per
simulazione del nuovo diviso quelli del vecchio, mediana per partita):

| variante                          | sim. | punti  | Elo  | IC 95%      | costo/sim |
|-----------------------------------|------|--------|------|-------------|-----------|
| 1. feature nuove, lineare (47)    | 500  | 51.0%  | +7   | −41..+56    | 1.49      |
|                                   | 2000 | 47.5%  | −17  | −66..+31    | 1.54      |
| 1. feature nuove + coppie         | 500  | 68.5%  | +135 | +86..+190   | 1.70      |
|                                   | 2000 | 58.5%  | +60  | +12..+110   | 1.70      |
| 1. solo coppie (38 feature)       | 500  | 58.5%  | +60  | +12..+110   | 1.08      |
|                                   | 2000 | 58.5%  | +60  | +12..+110   | 1.07      |
| 2. maestro guidato (38 feature)   | 500  | 65.5%  | +111 | +63..+165   | 1.00      |
|                                   | 2000 | 70.0%  | +147 | +97..+204   | 0.99      |
| **2. maestro guidato + coppie**   | 500  | 78.5%  | +225 | +171..+291  | 1.08      |
|                                   | 2000 | 75.2%  | +193 | +141..+255  | 1.08      |
| 2. maestro guidato + nuove + coppie | 500 | 79.5% | +235 | +181..+303  | 1.44      |
|                                   | 2000 | 73.0%  | +173 | +122..+232  | 1.47      |

(Le prime due righe con il codice delle feature nuove prima di un'ottimizzazione che
non cambia i valori; l'ultima dopo. Patte: una in tutto.)

Cosa dicono:

- **Il maestro conta più delle feature.** La strada 2 da sola vale +111 / +147 Elo senza
  costare nulla; le feature nuove da sole non valgono niente nonostante la perdita
  migliore (e costano 1.5 volte).
- **Le coppie di feature sono il guadagno della strada 1**, non le feature nuove: il
  modello lineare non sa dire "questa mossa è buona *se*" (avanzare di lato sulla fila di
  casa solo se c'è una minaccia, spingere una pedina avversaria vicina al traguardo solo
  se non apre una vittoria). Con le 38 feature costano l'8% in più per simulazione.
- Con il maestro guidato le feature nuove non aggiungono nulla di misurabile (79.5% e 73%
  contro 78.5% e 75.2%) e costano il 44% in più: con il maestro forte le coppie delle 38
  feature bastano.
- **Installata: maestro guidato + coppie sulle 38 feature** (`PRIOR_WEIGHTS` di
  `mcts.js`, `results/policy_weights_jsp20000.json`): +225 Elo a 500 simulazioni e +193 a
  2000 sul prior di prima, con il 75-79% dei punti, a un costo per simulazione dell'8% in
  più (a parità di tempo è ~0.1 raddoppi in meno, cioè ~10-15 Elo). Le feature nuove non
  sono entrate in `mcts.js`.

Contro la ricerca pura (100 partite per riga, `results/policy_v2_match_*`):

| guidato | puro  | punti del guidato | Elo  | IC 95%      | prima versione | costo/sim |
|---------|-------|-------------------|------|-------------|----------------|-----------|
| 500     | 2000  | 93.0%             | +449 | +346..+676  | 78.0%          | 1.41      |
| 500     | 4000  | 80.0%             | +241 | +165..+344  | 63.0%          | 1.44      |
| 500     | 8000  | 72.0%             | +164 | +94..+250   | 36.0%          | 1.51      |
| 500     | 16000 | 67.0%             | +123 | +55..+202   | —              | 1.48      |

**La seconda policy a 500 simulazioni batte la ricerca pura a 16000** (×32, 5
raddoppi), dove la prima pareggiava a ~5600 (2.5 raddoppi). Il costo per simulazione
rispetto alla pura è ~1.45 (la prima: ~1.3-1.4 nelle sue serie, con un altro carico
della macchina; nelle stesse partite il rapporto tra le due policy è 1.08).

## Cosa non è stato fatto

- I pesi valgono per le regole del sito (R = 4, N = 5, due mosse per turno, `ko`,
  `no_pushback`); con altre regole il prior è da riallenare.
- La policy non è usata nei playout (restano casuali con mosse decisive): costerebbe
  il calcolo delle feature a ogni semimossa del playout.
- `cPuct`, `fpu` e `priorTemp` sono tarati solo grossolanamente (40 partite per valore),
  e solo con la prima policy: la seconda è più concentrata (probabilità media della mossa
  più visitata 0.25 contro 0.17) e potrebbe volere un `cPuct` diverso.
- I livelli a tempo del sito usano il prior (`web/play/matches.py`, solo con le regole
  su cui è allenato): con la seconda policy fanno ~8% di simulazioni in meno nello stesso
  tempo. Non rimisurato nel browser né sui telefoni.
- Un solo giro di expert iteration. Il prossimo: corpus guidato dalla seconda policy,
  allenamento, misura contro questa. Le feature nuove (patch in `results/policy_v2/`)
  sono da riprovare solo se le coppie smettono di bastare.
- Il riuso dell'albero (`keep`, commit `5e82521` di main) funziona anche col prior
  (test in `TestJsPrior.test_tree_reuse`; un albero UCT non si riusa in una ricerca col
  prior né viceversa), ma il guadagno dei due insieme non è misurato.

## Come rifare tutto

```
pypy3 -m hexpush.policy corpus --ko --moves-per-turn 2 --sims 20000 --games 100 --jobs 6 \
    --out results/policy_corpus_js20000.jsonl
pypy3 -m hexpush.policy features --ko --moves-per-turn 2 results/policy_corpus_js20000.jsonl \
    --out /tmp/feature.jsonl
pypy3 -m hexpush.policy train /tmp/feature.jsonl --out results/policy_weights.json --install
pypy3 -m hexpush.policy match --ko --moves-per-turn 2 --sims 500 --factors 1,2,4,8,16 \
    --games 100 --out results/policy_match_500
node tests/js/bench.mjs 20000 prior < stato.json   # simulazioni al secondo con il prior

# seconda versione: maestro guidato dalla prima policy (base.mjs = mcts.js di 5e377a8),
# coppie di feature, confronto con la prima policy alle stesse simulazioni
git show 5e377a8:web/play/static/play/mcts.js > /tmp/base.mjs
pypy3 -m hexpush.policy corpus --ko --moves-per-turn 2 --sims 20000 --prior --js /tmp/base.mjs \
    --games 100 --jobs 4 --out results/policy_corpus_jsp20000.jsonl
pypy3 -m hexpush.policy features --ko --moves-per-turn 2 results/policy_corpus_jsp20000.jsonl \
    --out /tmp/feature2.jsonl
pypy3 -m hexpush.policy train /tmp/feature2.jsonl --pairs --l2 1e-3 --iters 150 \
    --out results/policy_weights_jsp20000.json --install
pypy3 -m hexpush.policy match --ko --moves-per-turn 2 --sims 500,2000 --games 200 \
    --base-js /tmp/base.mjs --out results/policy_v2/guidato_coppie
```
