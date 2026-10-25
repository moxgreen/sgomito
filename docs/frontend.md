# Frontend web — specifica v1

Gioco a due su browser con Django Channels, più partita contro il computer a
difficoltà variabile. Niente modalità "stesso schermo".

## Principio

Le regole stanno solo nel motore Python (`hexpush/`). Il server tiene lo stato
(`GameState`), valida ogni mossa e manda al browser lo **stato completo** dopo ogni
cambiamento. Il browser disegna e permette solo le mosse presenti nella lista delle
mosse legali che riceve: nessuna regola duplicata in JavaScript.

Regole di default: `Ruleset(ko=True, moves_per_turn=2)` (candidato attuale, vedi
CLAUDE.md). Chi crea la partita può scegliere varianti (`push_off`, `goal_wide`, `K`,
`R`, `N`) da una sezione "avanzate", per far provare le varianti.

## Stack

- Django + Channels (daphne), channel layer in memoria per ora: partite tenute in un
  dict nel processo. Persistenza e Redis più avanti.
- Frontend: un file HTML + un modulo JavaScript senza build, campo in SVG.
- `hexpush/` resta in Python puro senza dipendenze; Django solo in `web/`.

## Messaggi WebSocket

Browser → server:

```json
{"type": "move", "q": 1, "r": -3, "d": 2}
{"type": "undo"}
{"type": "resign"}
```

Server → browser, sempre lo stato completo (anche alla riconnessione):

```json
{"type": "state",
 "rules": {...}, "you": "A", "players": {"A": "umano", "B": "bot medio"},
 "board": [[q, r, "A"], ...],
 "to_move": "B", "moves_left": 2, "locked": null,
 "legal": [{"q": 0, "r": 4, "d": 3, "push": false, "to": [0, 3], "pushed_to": null}, ...],
 "ko_forbidden": [...],
 "last_turn": [{"q": ..., "r": ..., "d": ..., "player": "A"}, ...],
 "can_undo": false, "thinking": false,
 "result": null, "reason": null}
```

`to` e `pushed_to` (null o la cella dove finisce la pedina spinta, `"off"` con
push_off) li calcola il server, così il browser può disegnare l'anteprima delle spinte
senza conoscere la geometria delle regole.

## Interazione

- Campo ruotato per chi guarda: la propria casa in basso. Case e traguardi evidenziati,
  bordi-muro con tratto più spesso. Pedine distinguibili anche senza colore.
- Due modi equivalenti di muovere, entrambi con Pointer Events (mouse e touch):
  - **clic-clic**: clic sulla pedina, si illuminano le destinazioni (semplici e spinte in
    colori diversi), clic sulla destinazione;
  - **trascinamento**: si trascina la pedina, la destinazione legale più vicina si
    illumina, al rilascio la mossa parte; rilascio fuori da una destinazione legale =
    la pedina torna al suo posto.
- Anteprima della spinta: passando su (o trascinando verso) una spinta, freccia che
  mostra dove finisce la pedina spinta.
- Animazione breve di pedina e pedina spinta.
- Seconda mossa: pedina bloccata in grigio, indicatore "seconda mossa".
- **Annulla**: finché non è stata fatta la seconda mossa, la prima si può annullare
  (il server conserva lo stato d'inizio turno). Non vale dopo la fine del turno.
- Mosse vietate dal ko: mostrate barrate, con spiegazione al passaggio del mouse.
- Ultimo turno avversario: le sue due mosse disegnate come frecce.
- Fine partita: esito e motivo (traguardo, eliminazione, ripetizione, limite).

## Pagine

- **Home**: "Gioca con un amico" (crea una stanza, link da condividere, colore a scelta
  o casuale) e "Gioca contro il computer" (difficoltà, colore).
- **Partita**: `/partita/<id>/`. Chi apre il link occupa il posto libero; altri
  ospiti guardano. Riconnessione: si riceve lo stato e si riprende.

## Contro il computer

- Bot `mctsd` con il numero di simulazioni come difficoltà: Facile 200, Medio 1000,
  Difficile 4000, Esperto 12000.
- Livello Campione: alberi `mctsd` indipendenti, uno per ogni due core logici, che si
  dividono `HEXPUSH_CHAMPION_SIMS` simulazioni per mossa (default 192000), con un tetto
  di tempo per i dispositivi lenti (`HEXPUSH_CHAMPION_MAX_SECONDS`, default 15). Le
  visite delle mosse alla radice si sommano come in `ParallelMCTSBot`
  (`hexpush/parallel.py`). `n_sim` nella richiesta è il totale: il browser lo divide
  tra i worker. 192000 è la forza del vecchio Campione a tempo (3 s) su un Mac a 8 core,
  misurata nel browser: ~185000 simulazioni su 4 alberi. È il primo livello che l'autore
  non ha trovato troppo facile; Esperto (12000) lo era (ottobre 2026). Con 4 alberi sono
  ~3 s per mossa; con un worker solo serve un processore da ~13000 simulazioni al
  secondo per stare sotto il tetto.
- Livelli a tempo (`t1`, `t2`, `t5`, `t10`; `HEXPUSH_THINK_SECONDS`): chi gioca sceglie i
  secondi di riflessione per mossa, in media sulla partita. Tutti i core; prior acceso
  solo se le regole sono quelle su cui è allenato (`matches.PRIOR_RULES`: R=4, N=5, K=1,
  `ko`, `no_pushback`, due mosse per turno). Dove pensare lo decide la gestione del
  tempo `THINK_POLICY` (`TimePolicy` di `hexpush/mcts.py`, applicata da ogni worker al
  suo albero): `phase` dà ×1.3-1.5 a 3-5 file dal traguardo e ×0.5 in apertura
  (distanza 7-8), `early` ferma la ricerca quando la mossa più visitata non può più
  essere superata (con margine 0.5), `extend` la allunga fino a ×1.6 quando è instabile
  (results/thinktime_*). La media la tiene la **banca del tempo** (`think_budget`): il
  limite di ogni mossa è `secondi × (tempo che resta per pareggiare la media) / secondi`,
  limitato tra ×0.5 e ×1.4; i secondi spesi vengono da `info.ms` del registro (le mosse
  obbligate o vincenti non contano), quindi la banca sopravvive a un riavvio. Una mossa
  dura al più ~3.4 volte la media. Prova nel browser (livello 2 s, Mac a 8 core): 15
  mosse pensate tra 0.5 e 3.6 s, media 1.89 s.
- Il bot calcola **nel browser** del giocatore umano, in Web Worker
  (`web/play/static/play/mcts.js`, porting di `hexpush/mcts.py`). Quando tocca al
  computer, lo stato di quel giocatore ha `"thinking": true` e la richiesta
  `"bot": {"ply", "n_sim", "time_limit", "workers", "delay", "search"}`; `search` è
  `serialize.search_state_to_dict`. Il browser risponde
  `{"type": "bot_move", "ply", "q", "r", "d"}`. Il server la valida come le mosse
  umane; una risposta con un `ply` superato si ignora. Chi guarda non riceve `bot`.
- Ogni Web Worker tiene l'albero della sua ultima ricerca (`search(..., {keep})` in
  `mcts.js`): se la nuova posizione ne discende (la seconda mossa del turno, o la prima
  dopo le due dell'avversario: al più 4 semimosse, seguendo `history`), la ricerca
  riparte da quel sottoalbero e `n_sim` / il tempo contano solo le simulazioni nuove.
  Misurato col motore JS: a 13500 simulazioni per albero il riuso scatta nel 94% delle
  ricerche e aggiunge in media il 25% di visite (19% e 15% a 4000).
- Le due mosse del bot vengono mostrate una alla volta: il browser aspetta almeno
  `delay` secondi prima di mandarle.

## Test

- Serializzazione dello stato (JSON) e calcolo di `to`/`pushed_to` in `hexpush/` o
  `web/`, con test.
- Consumer WebSocket con `channels.testing.WebsocketCommunicator`: mossa legale,
  mossa illegale rifiutata, turno sbagliato rifiutato, annulla, fine partita, bot che
  risponde.
- I test esistenti (`python3 -m unittest`) devono continuare a passare.
