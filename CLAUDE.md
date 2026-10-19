# Gioco di spinta su esagoni — motore e self-play

## Contesto

Progetto didattico. Obiettivo finale: gioco a due su browser con Django Channels (WebSocket).
Obiettivo di **questa fase**: motore di gioco in Python puro + bot che giocano tra loro
per capire se le regole funzionano e per ottimizzare i parametri. Niente Django per ora.

Il gioco non è mai stato playtestato. Le regole qui sotto sono la versione 1, volutamente
minimale. Le decisioni di design si prendono in base ai numeri del self-play, non a priori.

## Regole v1

- Campo a forma di **esagono regolare** di raggio `R`, in coordinate assiali `(q, r)`:
  le celle sono quelle con `|q| <= R`, `|r| <= R`, `|q + r| <= R`
  (con `R = 4` sono 61 celle, come Abalone). Le sei direzioni sono
  `(+1,0) (-1,0) (0,+1) (0,-1) (+1,-1) (-1,+1)`.
- Due giocatori, A e B. La fila di casa di A è il bordo `r = -R`, quella di B è il
  bordo `r = R`. Ciascuna è lunga `R + 1` celle. Gli altri quattro bordi sono muri.
  L'avanzamento di una pedina è la sua coordinata `r` (per A cresce, per B decresce).
- Geometria da tenere presente: le file di casa sono strette (`R + 1`), il centro è
  largo (`2R + 1`). La difesa della casa potrebbe risultare facile: vedi domande aperte.
- Ogni giocatore ha `N` pedine, piazzate all'inizio sulla propria fila di casa,
  centrate. Se `N > R + 1` le pedine in eccesso vanno nella fila successiva, centrate.
- A muove per primo. A ogni turno il giocatore sposta **una** propria pedina
  in una delle sei caselle adiacenti:
  - se la casella è vuota, la pedina ci si sposta;
  - se la casella è occupata (da qualunque colore) è una **spinta**: è legale solo se
    la casella successiva nella stessa direzione esiste ed è vuota. La pedina spinta
    si sposta lì e la pedina che muove prende il suo posto.
  - Non si spinge mai più di una pedina. Non si spinge mai fuori dal campo
    (il bordo è un muro). Non si cattura mai.
- Vittoria: una propria pedina arriva sulla fila di casa dell'avversario
  (parametro `K` = numero di pedine da portare di là, default 1). Con `goal_wide`
  contano anche le celle dei due bordi laterali immediatamente adiacenti alla casa.
- Patta: stessa posizione (campo + giocatore di turno) ripetuta 3 volte,
  oppure superate `MAX_PLIES` semimosse (default 200).
- Se un giocatore non ha mosse legali, passa il turno (caso raro, va solo gestito).

### Dettagli decisi in implementazione

- Dopo ogni mossa si controlla il traguardo di **entrambi**: se una spinta porta una
  pedina avversaria sulla sua fila d'arrivo, vince l'avversario. Se arrivano tutti e due
  nella stessa mossa vince chi ha mosso. La vittoria ha la precedenza sulla patta.
- Con `K > 1` contano le pedine **presenti ora** sul traguardo. Nota: una pedina sulla
  fila di casa nemica non può essere spinta fuori da quella fila (dietro c'è il muro);
  solo le celle extra di `goal_wide` si possono svuotare con una spinta.
- `goal_wide` aggiunge una cella per lato: quella del bordo laterale subito accanto
  all'angolo della casa nemica. Per A, con `R = 4`: `(-4, 3)` e `(1, 3)`.
- Posizione iniziale: B è l'immagine di A per riflessione nel centro `(q,r) -> (-q,-r)`,
  così il campo resta simmetrico anche quando `N` non si centra esattamente.
- Patta per limite: a `ply == max_plies` senza vincitore. La ripetizione conta anche la
  posizione iniziale. Il passo del turno è la mossa `PASS = None`.
- Vantaggio del primo giocatore: % di vittorie di A sulle partite **decise**; il tasso di
  patta si riporta a parte.

## Parametri da esplorare

| nome        | default | da provare            | note                                   |
|-------------|---------|-----------------------|----------------------------------------|
| `R`         | 4       | 3, 4, 5               | raggio: 37, 61, 91 celle               |
| `N`         | 5       | 3 … 8                 | pedine per giocatore                   |
| `goal_wide` | False   | True, False           | traguardo esteso: anche le 2 celle dei bordi laterali adiacenti alla casa nemica |
| `K`         | 1       | 1, 2                  | pedine da portare di là per vincere    |
| `push_own`  | True    | True, False           | si possono spingere le proprie pedine  |
| `ko`        | False   | True, False           | vietato riportare la posizione di 2 semimosse fa |
| `push_off`  | False   | True, False           | spingere fuori dal campo una pedina avversaria la elimina; chi resta con meno di K pedine perde |
| `moves_per_turn` | 1  | 1, 2                  | 2: due mosse per turno con pedine diverse, A apre con una sola; il ko vale a fine turno |
| `MAX_PLIES` | 200     | —                     | limite anti-partite infinite (in semimosse) |

Tutti i parametri stanno in una `Ruleset` (dataclass frozen). Nessuna variante va
implementata come fork del codice: solo come configurazione.

## Metriche (per ogni configurazione, su >= 2000 partite)

1. **Tasso di patta** (ripetizione + limite mosse, separati). Soglia: < 10%.
2. **Lunghezza media e distribuzione** in semimosse. Obiettivo: 30–80.
3. **Vantaggio del primo giocatore** (% vittorie di A a parità di bot). Soglia: < 58%.
4. **Frequenza delle spinte** sul totale delle mosse. Se è vicina a zero la meccanica
   centrale non serve e il gioco è una corsa; se è altissima è un ping-pong.
5. **Vittorie greedy vs random**: se il greedy non domina nettamente, il gioco ha poca
   strategia o il greedy è scritto male.
6. **Scala di forza**: Greedy vs patologici, MCTS forte vs MCTS debole (vedi sezione Bot).

Attenzione: le conclusioni dipendono dalla qualità dei bot. Un gioco che finisce
sempre in patta tra bot stupidi può essere ottimo tra umani (e viceversa).
Quindi: prima fidarsi solo dei segnali grossolani, poi migliorare i bot.

## Bot: una scala, non un bot solo

I bot servono a domande diverse. Non costruire il livello successivo finché il
precedente non ha risposto alla sua domanda.

### Livello 1 — motore (Random, Greedy)

- `RandomBot`: mossa legale a caso.
- `GreedyBot`: 1 ply, euristica
  `somma(avanzamento pedine proprie) - somma(avanzamento pedine avversarie)`,
  avanzamento = `r + R` per A, `R - r` per B. Vittoria immediata = +inf.
  Pareggi tra mosse a punteggio uguale rotti a caso.

Rispondono a: il motore è corretto? quanto sono lunghe le partite? la spinta viene
usata? quanto è veloce il codice? **Non** dicono nulla sulla profondità del gioco:
l'euristica incorpora i pregiudizi di chi la scrive.

### Livello 2 — bot patologici (test delle regole)

Bot deboli ma specializzati in una strategia degenere. Ogni modifica alle regole si
valuta facendoli giocare contro il Greedy (e tra loro):

- `TurtleBot`: non porta mai pedine oltre la seconda fila; usa le mosse per tappare e
  spingere via chi si avvicina. Se non perde quasi mai → la difesa è troppo forte.
- `RusherBot`: avanza una sola pedina il più in fretta possibile, le altre ferme.
  Se vince spesso → il gioco è una corsa, la spinta non conta.
- `PingPongBot`: se una sua pedina è stata spinta, la rispinge; altrimenti greedy.
  Misura quanto serve la regola di ripetizione / il `ko`.

Soglie indicative: Turtle e Rusher devono perdere contro il Greedy almeno il 70%
delle volte. Se uno dei due non perde, prima si corregge quella regola, poi si
va avanti.

### Livello 3 — MCTS (profondità del gioco)

`MCTSBot(n_sim)`: Monte Carlo Tree Search con UCT e playout casuali fino alla fine
della partita (o a `MAX_PLIES`, contando patta = 0.5). Nessuna euristica: scopre la
strategia invece di assumerla.

Domanda a cui risponde: **il gioco premia il pensare?** Si confronta
`MCTSBot(2000)` contro `MCTSBot(100)` su qualche centinaio di partite:
- vince ~55% → poca profondità, segnale brutto;
- vince >80% → c'è strategia, segnale buono.
Si guarda anche MCTS(2000) contro Greedy e contro i patologici.

Prestazioni: in Python puro ~1 s/mossa a 500 simulazioni; qualche centinaio di
partite, non migliaia. Ordine degli interventi se è troppo lento:
1. `pypy3 simulate.py` (nessuna modifica al codice, tipicamente 4–10x);
2. playout troncati a 40 semimosse con valutazione = differenza di avanzamento;
3. solo dopo, profilare e ottimizzare `legal_moves`/`apply`;
4. motore in Rust/C solo se davvero necessario (probabilmente no).

Non implementare minimax con euristica: ha lo stesso difetto del Greedy.

Il test vero di profondità è la **curva a raddoppi** in `docs/curva_raddoppi.md`
(prevale su questa sezione dove diverge), implementata in `hexpush/ladder.py`.

### Cosa i bot non possono dire

Se il gioco è divertente. Possono solo escludere che sia rotto (patte, corse,
difesa invincibile, vantaggio del primo). Quando i numeri sono buoni si passa agli
umani, e il `MCTSBot` diventa l'avversario per la modalità solitario.

## Architettura

```
hexpush/
  rules.py       # Ruleset (dataclass frozen)
  board.py       # celle dell'esagono di raggio R, direzioni, vicini, bordi, posizioni iniziali
  game.py        # GameState, legal_moves(state), apply(state, move), winner(state)
  bots.py        # Random, Greedy, Turtle, Rusher, PingPong, MCTS (interfaccia: choose(state) -> move)
  mcts.py        # MCTS su FastBoard (campo ad array, hash di Zobrist); mcts = puro, mctsd = mosse decisive + c=0.5
  simulate.py    # CLI: --R --N --K ... --games --botA --botB --seed, scrive CSV
  sweep.py       # griglia di parametri, chiama simulate, aggrega risultati
  ladder.py      # curva a raddoppi MCTS (docs/curva_raddoppi.md), --against BOT
  watch.py       # guarda una partita in ASCII
tests/
  test_game.py   # mosse legali, spinte, bordo, vittoria, ripetizione
  test_bots.py   # bot, e coerenza tra FastBoard e il motore di riferimento
```

Self-play con `pypy3` (playout ~12x più veloci); i test girano anche con python3.

Vincoli di implementazione:

- `GameState` immutabile (tuple/frozenset) o copiato ad ogni `apply`: serve per
  l'albero MCTS e per contare le ripetizioni con un hash.
- Il campo si rappresenta come `dict[(q,r)] -> 'A'|'B'`; le celle vuote non ci sono.
- Le mosse sono `(q, r, direzione)`. `legal_moves` restituisce una lista; una mossa
  include già l'informazione se è una spinta.
- Tutto deterministico dato un seed: `random.Random(seed)` passato ai bot.
- Nessuna dipendenza oltre la stdlib (numpy/pandas opzionali solo in `sweep.py`).
- Prima i test, poi il self-play. I casi da coprire assolutamente: spinta verso il
  bordo illegale, spinta con casella dietro occupata illegale, spinta di pedina propria
  secondo `push_own`, vittoria rilevata appena una pedina tocca la fila nemica,
  ripetizione tripla.

## Piano in passi

1. `board.py` + `rules.py` + `game.py` con test. Verificare a mano una partita
   stampando il campo in ASCII (una funzione `render(state)` aiuta molto il debug).
2. `bots.py` con Random e Greedy. `simulate.py` che gioca `--games` partite e stampa
   le 5 metriche. Obiettivo di velocità: almeno 200 partite/secondo con i default;
   se è più lento, profilare `legal_moves` prima di ottimizzare altro.
3. `sweep.py` sulla griglia della tabella con Random/Greedy. Guardare i numeri e
   decidere se le regole v1 reggono o serve un correttivo (candidati, in ordine:
   `ko`, `goal_wide`, `K=2`, regola di maggioranza stile Abalone, "stordimento"
   della pedina spinta che salta un turno).
4. Bot patologici: ogni regola candidata si valuta contro di loro prima di adottarla.
5. `MCTSBot` a due livelli di simulazioni per misurare la profondità. Se il forte batte
   nettamente il debole e i patologici perdono, si passa a Django.

## Risultati (ottobre 2026)

Dati in `results/`. Bot MCTS = `mctsd` (mosse decisive nei playout, c=0.5): il
`mcts` puro con c=1.4 batteva il Greedy solo 63-67% e distribuiva le visite quasi
uniformemente.

1. **Regole v1 senza `ko`**: Greedy contro Greedy patta per ripetizione nel 67%
   (ping-pong di spinte). `ko` è necessario in tutte le varianti.
2. **v1 + `ko`** (R=4, N=5): curva a raddoppi piatta, ~125-135 Elo per raddoppio fino a
   3200 contro 1600 (69%); A 52% tra MCTS; patte ~0. Ma il **Rusher** batte MCTS(2000)
   (33% per MCTS) e anche Greedy e Turtle. Con più ricerca si batte: MCTS 500 / 2000 /
   8000 / 32000 contro Rusher = 9% / 28% / 70% / 95%. La corsa è una trappola che
   richiede molto calcolo per essere parata.
   Perché funziona: la propria fila di casa è un muro invulnerabile (dietro c'è il
   bordo), le pedine ferme a casa chiudono il traguardo avversario, ogni spinta
   difensiva porta il difensore fuori dal muro, e una pedina in fila R-1 minaccia due
   celle.
3. **`moves_per_turn=2` + `ko`**: MCTS(2000) batte il Rusher 95% (contro 28% con una
   mossa), Greedy 82%, Turtle 99%, PingPong 100%. Raddoppi 250-2000: ~115-155 Elo per
   raddoppio. A 47% tra MCTS, patte 0, ~50 semimosse (25 turni), spinte 28% delle mosse.
   Greedy contro Greedy: A 50%, patte 0. Rusher contro Rusher: A 57%.
4. `push_off` implementato ma non ancora misurato. Rischio: una pedina in fila R-1 che
   butta fuori un difensore dalla sua casa ne prende il posto e vince subito.

5. **Protocollo completo su v1 + `ko` + `moves_per_turn=2`** (`results/ladder_mpt2_*`,
   200 partite per incontro): Elo per raddoppio 117 / 119 / 108 / 70 / 143 da 100-200 a
   1600-3200 (3200 contro 1600 vince il 69.5%). MCTS(2000) contro Greedy 80%, Turtle
   99.5%, Rusher 94%, PingPong 100%. Tra MCTS: A 53.8% (±3.1), patte 0.1%, ~50-60
   semimosse, spinte 27% delle mosse. Tutte le soglie superate.

Candidato attuale: **v1 + `ko` + `moves_per_turn=2`**. Secondo il protocollo si può
passare agli umani e a Django. Da tenere d'occhio: leggero vantaggio di A (53.8%).

## Domande ancora aperte (da decidere con i dati, non adesso)

- La casa stretta favorisce troppo la difesa? Se sì, prima `goal_wide`, poi `K`.
- Verificare comunque che, scambiando chi muove per primo, le percentuali di A e B
  si invertano: controlla che il campo e la posizione iniziale siano davvero simmetrici.
- Serve la regola di maggioranza di Abalone (2 pedine spingono una pedina coperta)?
- La spinta deve dare un vantaggio di tempo (pedina spinta ferma un turno)?
- Più di due giocatori: non ora.
