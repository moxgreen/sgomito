# Frontend web

Django + Channels (daphne). Specifica in `docs/frontend.md`. Le regole stanno solo in
`hexpush/`: il server valida le mosse e manda al browser lo stato completo
(`hexpush/serialize.py`).

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
`HEXPUSH_HOSTS`, `HEXPUSH_DB` (file SQLite, default `web/db.sqlite3`), `HEXPUSH_BOT_DELAY` (pausa minima in secondi prima di ogni mossa del
computer, default `0.7`), `HEXPUSH_CHAMPION_SECONDS` (secondi di ricerca per mossa del
livello Campione, default `3`), `HEXPUSH_PYPY` (interprete per i suoi processi, default
`pypy3` se c'è, altrimenti lo stesso Python del server).

Il livello Campione (`hexpush/parallel.py`) fa girare più alberi MCTS in processi
separati, metà dei core logici, con pypy3 se installato (`brew install pypy3`): circa
20 volte le simulazioni dell'Esperto nello stesso tempo. Senza pypy3 funziona lo stesso,
ma fa circa 8 volte meno simulazioni nello stesso tempo. I processi partono alla prima mossa
del Campione e restano vivi; due partite contro il Campione nello stesso momento fanno
la fila.

## Test

```bash
.venv/bin/python web/manage.py test play     # viste e consumer WebSocket
python3 -m unittest                          # motore e serializzazione, senza Django
```

## File

- `hexweb/`: impostazioni, URL, ASGI (HTTP + WebSocket).
- `play/matches.py`: partite in memoria, posti, annullamento, livelli del computer.
- `play/models.py`, `play/records.py`: registro su database (Player, Game, Rating) e
  ricostruzione delle partite dopo un riavvio.
- `play/views.py`: home, nome, partita, registro e CSV; `manage.py registro` esporta i CSV.
- `play/consumers.py`: WebSocket `/ws/partita/<id>/`, bot in un thread.
- `play/static/play/game.js`: campo SVG, clic-clic e trascinamento (Pointer Events).
