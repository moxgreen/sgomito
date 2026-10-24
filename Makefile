.PHONY: help venv migrate run run-lan shell check test test-engine test-web test-js bench-js registry
.PHONY: simulate watch ladder gifs clean

# --- Configurazione (si può sovrascrivere: make run PORT=9000) ---
VENV    := .venv
PYTHON  := $(VENV)/bin/python
# self-play con pypy3 se c'è (playout ~12x più veloci), altrimenti python3
PYPY    ?= $(shell command -v pypy3 || echo python3)
HOST    ?= 127.0.0.1
PORT    ?= 8000
LAN_IP  ?= $(shell ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | cut -d' ' -f1)

# regole del candidato attuale (CLAUDE.md) e parametri del self-play
RULES   ?= --ko --moves-per-turn 2
BOTA    ?= mctsd:2000
BOTB    ?= greedy
GAMES   ?= 200
SEED    ?= 0
DELAY   ?= 0.5
OUT     ?= results/ladder_$(shell date +%Y%m%d-%H%M%S)

help:
	@echo ""
	@echo "Sviluppo web:"
	@echo "  make help                 - Mostra questo elenco"
	@echo "  make venv                 - Crea .venv e installa web/requirements.txt"
	@echo "  make migrate              - Crea/aggiorna il database del registro (web/db.sqlite3)"
	@echo "  make run                  - Avvia il server Django/Channels su http://$(HOST):$(PORT)/"
	@echo "  make run-lan              - Avvia il server visibile in rete locale ($(LAN_IP):$(PORT))"
	@echo "  make shell                - Apre la shell di Django"
	@echo "  make check                - Controlli di configurazione di Django"
	@echo "  make registry             - Scarica partite e giudizi in results/playtest_*.csv"
	@echo "  make gifs                 - Rigenera le GIF di \"Come si gioca\" (installa Pillow nel venv)"
	@echo ""
	@echo "Test:"
	@echo "  make test                 - Tutti i test (motore + web)"
	@echo "  make test-engine          - Test del motore hexpush/ (python3, senza dipendenze)"
	@echo "  make test-web             - Test delle viste e del consumer WebSocket"
	@echo "  make test-js              - Coerenza del motore JavaScript del browser (serve node >= 22)"
	@echo "  make bench-js             - Simulazioni al secondo del computer nel browser (node)"
	@echo ""
	@echo "Self-play (con $(PYPY)):"
	@echo "  make simulate             - $(BOTA) contro $(BOTB), $(GAMES) partite per colore"
	@echo "  make watch                - Guarda una partita $(BOTA) contro $(BOTB) in ASCII"
	@echo "  make ladder               - Curva a raddoppi MCTS (docs/curva_raddoppi.md) in results/"
	@echo ""
	@echo "Pulizia:"
	@echo "  make clean                - Cancella __pycache__ e .pyc (non tocca .venv né results/)"
	@echo ""
	@echo "Parametri:"
	@echo "  make simulate BOTA=greedy BOTB=random GAMES=2000 RULES='--ko --goal-wide'"
	@echo "  make run PORT=9000        make watch BOTA=mctsd:500 BOTB=rusher DELAY=0.2"
	@echo ""

# --- Sviluppo web ---

venv: $(VENV)/.installato

$(VENV)/.installato: web/requirements.txt
	test -x $(PYTHON) || python3 -m venv $(VENV)
	$(PYTHON) -m pip install -q -r web/requirements.txt
	@touch $@

migrate: venv
	$(PYTHON) web/manage.py migrate

run: migrate
	$(PYTHON) web/manage.py runserver $(HOST):$(PORT)

run-lan: migrate
	@echo "Dal telefono: http://$(LAN_IP):$(PORT)/"
	HEXPUSH_HOSTS=$(LAN_IP) $(PYTHON) web/manage.py runserver 0.0.0.0:$(PORT)

shell: venv
	$(PYTHON) web/manage.py shell

check: venv
	$(PYTHON) web/manage.py check

# il registro del playtest in CSV, letto dal database (anche a server spento)
registry: migrate
	$(PYTHON) web/manage.py registro --out results/playtest

# le GIF degli esempi di "Come si gioca", giocate sul motore (web/tools/gif_regole.py)
gifs: venv
	$(PYTHON) -m pip install -q Pillow
	$(PYTHON) web/tools/gif_regole.py

# --- Test ---

test: test-engine test-web

test-engine:
	python3 -m unittest

test-web: venv
	$(PYTHON) web/manage.py test play

# già compreso in test-engine (si salta se node manca)
test-js:
	python3 -m unittest -v tests.test_js_engine

bench-js:
	python3 -c 'import json; from hexpush.game import new_game; from hexpush.rules import Ruleset; \
	from hexpush.serialize import search_state_to_dict; \
	print(json.dumps(search_state_to_dict(new_game(Ruleset(ko=True, no_pushback=True, moves_per_turn=2)))))' \
	| node tests/js/bench.mjs 20000

# --- Self-play ---

simulate:
	$(PYPY) -m hexpush.simulate $(RULES) --botA $(BOTA) --botB $(BOTB) --games $(GAMES) --seed $(SEED) --swap

watch:
	$(PYPY) -m hexpush.watch $(RULES) --botA $(BOTA) --botB $(BOTB) --seed $(SEED) --delay $(DELAY)

ladder:
	@mkdir -p results
	$(PYPY) -m hexpush.ladder $(RULES) --games $(GAMES) --seed $(SEED) --out $(OUT)
	@echo "Risultati in $(OUT)_*"

# --- Pulizia ---

clean:
	find . -path ./$(VENV) -prune -o -name __pycache__ -type d -exec rm -rf {} +
	find . -path ./$(VENV) -prune -o -name '*.pyc' -type f -delete
