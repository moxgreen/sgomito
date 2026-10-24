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

- Bot `mctsd` con il numero di simulazioni come difficoltà, per esempio:
  Facile 200, Medio 1000, Difficile 4000, Esperto 16000 (da tarare sui tempi reali
  del server; con CPython il nuovo albero fa ~0.1-0.4 s per 1000 simulazioni).
- Livello Campione: `ParallelMCTSBot` (`hexpush/parallel.py`), alberi `mctsd`
  indipendenti in processi pypy3 paralleli con un limite di tempo per mossa
  (`HEXPUSH_CHAMPION_SECONDS`); il padre somma le visite delle mosse alla radice.
- Il bot calcola fuori dal ciclo degli eventi (`asyncio.to_thread` o un worker); nel
  frattempo lo stato ha `"thinking": true` e la pagina mostra "sta pensando".
- Le due mosse del bot vengono mostrate una alla volta, con una breve pausa.

## Test

- Serializzazione dello stato (JSON) e calcolo di `to`/`pushed_to` in `hexpush/` o
  `web/`, con test.
- Consumer WebSocket con `channels.testing.WebsocketCommunicator`: mossa legale,
  mossa illegale rifiutata, turno sbagliato rifiutato, annulla, fine partita, bot che
  risponde.
- I test esistenti (`python3 -m unittest`) devono continuare a passare.
