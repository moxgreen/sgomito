# Frontend web

Django + Channels (daphne). Specifica in `docs/frontend.md`. Le regole stanno solo in
`hexpush/`: il server valida le mosse e manda al browser lo stato completo
(`hexpush/serialize.py`).

Il computer gioca **nel browser**, non sul server. Quando tocca a lui, lo stato mandato
al giocatore umano contiene la richiesta `bot`: parametri del livello, semimossa e stato
completo, storia compresa. La pagina fa girare la ricerca MCTS in Web Worker
(`play/static/play/mcts.js`, porting di `hexpush/mcts.py`, tenuto allineato da
`tests/test_js_engine.py`) e risponde con `bot_move`. Il server valida la mossa con
`legal_moves` come quelle degli umani, e ignora le risposte arrivate in ritardo, per
esempio da una seconda scheda. Se l'umano chiude la pagina mentre tocca al computer, la
partita aspetta e riprende quando la riapre.

Da sapere quando si leggono i dati del registro: le mosse del computer le manda il
browser dell'avversario. Chi volesse barare potrebbe scegliersele, ma solo tra quelle
legali.

È una piattaforma di playtest: chi gioca scrive il proprio nome, ogni partita viene
registrata in SQLite (`web/db.sqlite3`: giocatori, mosse con i tempi, annullamenti,
esito) e a fine partita, anche dopo un abbandono, ciascun giocatore dà un giudizio sul
divertimento da 1 a 5 stelle, con un commento facoltativo. Le partite in corso stanno in
memoria e dopo un riavvio del server si ricostruiscono dal database rigiocando le mosse.

Il registro si legge su `/registro/` (riassunto per variante di regole, tabella delle
partite, `partite.csv` e `giudizi.csv`, una partita in JSON su `/registro/<id>.json`)
oppure da riga di comando con `make registry`, che scrive `results/playtest_*.csv`.
La pagina non è protetta: chiunque arrivi al server la vede.

## Avvio in locale

Scorciatoie nel `Makefile` della radice: `make venv`, `make run` (applica anche le
migrazioni), `make run-lan`, `make test`, `make registry` (`make help` per l'elenco). A mano:

Dalla radice del repository, una volta sola:

```bash
python3 -m venv .venv && .venv/bin/pip install -r web/requirements.txt
```

Poi:

```bash
.venv/bin/python web/manage.py migrate
.venv/bin/python web/manage.py runserver
```

e si apre http://localhost:8000/. Per giocare da un altro dispositivo della stessa rete:

```bash
HEXPUSH_HOSTS=192.168.1.10 .venv/bin/python web/manage.py runserver 0.0.0.0:8000
```

(con l'IP del computer al posto di `192.168.1.10`).

Il giocatore è riconosciuto da un cookie (`hexpush_pid`): due schede dello stesso browser
sono lo stesso giocatore. Per provare una partita tra amici da soli serve un secondo
browser o una finestra in incognito.

Variabili d'ambiente: `HEXPUSH_SECRET_KEY`, `HEXPUSH_DEBUG` (default `1`),
`HEXPUSH_HOSTS`, `HEXPUSH_DB` (file SQLite, default `web/db.sqlite3`), `HEXPUSH_BOT_DELAY`
(pausa minima in secondi prima di ogni mossa del computer, default `0.7`) e
`HEXPUSH_CHAMPION_SECONDS` (secondi di ricerca per mossa del livello Campione, default `3`).

I livelli Facile, Medio, Difficile ed Esperto hanno un numero fisso di simulazioni
(200, 1000, 4000, 12000): la forza è la stessa su qualunque dispositivo e cambia solo
l'attesa. Il JavaScript fa ~15000-25000 simulazioni al secondo per albero (misurati con `make
bench-js` dall'apertura), contro ~5000 di pypy3 e ~1000 di CPython: anche
l'Esperto risponde in meno di un secondo. Il livello Campione invece lavora a tempo:
alberi indipendenti su metà dei core logici (al massimo 8), con le visite sommate come
in `hexpush/parallel.py`. La sua forza dipende quindi dal dispositivo di chi gioca.

## Test

```bash
.venv/bin/python web/manage.py test play     # viste e consumer WebSocket
python3 -m unittest                          # motore e serializzazione, senza Django
make test-js                                 # solo la coerenza del motore JavaScript
```

`tests/test_js_engine.py` prepara con `hexpush/game.py` partite casuali su tutte le
varianti di regole e le fa ricontrollare a `tests/js/check_engine.mjs`, con node:
mosse legali, vittorie immediate, ko, rispinte vietate, ripetizioni e limite di
semimosse. Senza node (almeno la versione 22) il test si salta.

## File

- `hexweb/`: impostazioni, URL, ASGI (HTTP + WebSocket).
- `play/matches.py`: partite in memoria, posti, annullamento, livelli del computer.
- `play/models.py`, `play/records.py`: registro su database (Player, Game, Rating) e
  ricostruzione delle partite dopo un riavvio.
- `play/views.py`: home, nome, partita, registro e CSV; `manage.py registro` esporta i CSV.
- `play/consumers.py`: WebSocket `/ws/partita/<id>/`, mosse degli umani e del computer.
- `play/static/play/game.js`: campo SVG, clic-clic e trascinamento (Pointer Events).
- `play/static/play/computer.js`, `bot-worker.js`, `mcts.js`: il computer nel browser
  (Web Worker, ricerca MCTS, somma delle visite tra alberi paralleli).
