"""Impostazioni del frontend web. Le partite in corso stanno in memoria; il registro
del playtesting (giocatori, nickname registrati, partite, giudizi) in SQLite."""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# il motore hexpush/ sta nella radice del repository, accanto a web/
sys.path.insert(0, str(BASE_DIR.parent))

SECRET_KEY = os.environ.get("HEXPUSH_SECRET_KEY", "solo-per-sviluppo-non-usare-in-produzione")
DEBUG = os.environ.get("HEXPUSH_DEBUG", "1") == "1"
# altri host (es. l'IP del portatile per giocare dal telefono): HEXPUSH_HOSTS=192.168.1.10
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"] + [
    h for h in os.environ.get("HEXPUSH_HOSTS", "").split(",") if h]

INSTALLED_APPS = [
    "daphne",  # runserver ASGI (WebSocket compresi)
    "django.contrib.staticfiles",
    "channels",
    "play",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "hexweb.urls"
ASGI_APPLICATION = "hexweb.asgi.application"
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": os.environ.get("HEXPUSH_DB", str(BASE_DIR / "db.sqlite3")),
}}

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": ["django.template.context_processors.request",
                                       "play.context.assets"]},
}]

LANGUAGE_CODE = "en"
TIME_ZONE = "Europe/Rome"
USE_I18N = False
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# pausa minima (secondi) prima di ogni mossa del computer, per vederle una alla volta
HEXPUSH_BOT_DELAY = float(os.environ.get("HEXPUSH_BOT_DELAY", "0.7"))
# livello Campione: simulazioni per mossa in totale, divise tra i Web Worker (uno per ogni
# due core), e tetto in secondi per i dispositivi lenti
HEXPUSH_CHAMPION_SIMS = int(os.environ.get("HEXPUSH_CHAMPION_SIMS", "192000"))
HEXPUSH_CHAMPION_MAX_SECONDS = float(os.environ.get("HEXPUSH_CHAMPION_MAX_SECONDS", "15"))
# livelli a tempo: secondi di riflessione per mossa, in media sulla partita, tra cui sceglie
# chi gioca (il computer pensa meno in apertura e quando la mossa è chiara, di più dove
# conta, e recupera il tempo risparmiato)
HEXPUSH_THINK_SECONDS = [float(x) for x in
                         os.environ.get("HEXPUSH_THINK_SECONDS", "1,2,5,10").split(",")]
