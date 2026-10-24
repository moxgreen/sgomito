"""manage.py registro --out results/playtest: scrive <out>_partite.csv e <out>_giudizi.csv."""
from pathlib import Path

from django.core.management.base import BaseCommand

from play.models import Game, Rating
from play.views import write_games_csv, write_ratings_csv


class Command(BaseCommand):
    help = "Esporta in CSV le partite registrate e i giudizi sul divertimento."

    def add_arguments(self, parser):
        parser.add_argument("--out", default="playtest",
                            help="prefisso dei file (default: playtest)")

    def handle(self, *args, out, **options):
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        for suffix, writer in (("partite", write_games_csv), ("giudizi", write_ratings_csv)):
            path = f"{out}_{suffix}.csv"
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer(f)
            self.stdout.write(f"scritto {path}")
        self.stdout.write(f"{Game.objects.count()} partite, {Rating.objects.count()} giudizi")
