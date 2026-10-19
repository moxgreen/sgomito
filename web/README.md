# Frontend web

Django + Channels (daphne). Specifica in `docs/frontend.md`. Le regole stanno solo in
`hexpush/`: il server valida le mosse e manda al browser lo stato completo
(`hexpush/serialize.py`). Le partite stanno in memoria: un riavvio del server le perde.

## Avvio in locale

Dalla radice del repository, una volta sola:

```bash
python3 -m venv .venv && .venv/bin/pip install -r web/requirements.txt
```

Poi:

```bash
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
`HEXPUSH_HOSTS`, `HEXPUSH_BOT_DELAY` (pausa minima in secondi prima di ogni mossa del
computer, default `0.7`).

## Test

```bash
.venv/bin/python web/manage.py test play     # viste e consumer WebSocket
python3 -m unittest                          # motore e serializzazione, senza Django
```

## File

- `hexweb/`: impostazioni, URL, ASGI (HTTP + WebSocket).
- `play/matches.py`: partite in memoria, posti, annullamento, livelli del computer.
- `play/consumers.py`: WebSocket `/ws/partita/<id>/`, bot in un thread.
- `play/static/play/game.js`: campo SVG, clic-clic e trascinamento (Pointer Events).
