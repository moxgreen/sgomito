"""manage.py registry --out results/playtest: scrive <out>_games.csv e <out>_ratings.csv."""
from pathlib import Path

from django.core.management.base import BaseCommand

from play.models import Game, Rating
from play.views import write_games_csv, write_ratings_csv


class Command(BaseCommand):
    help = "Export the recorded games and the fun ratings as CSV."

    def add_arguments(self, parser):
        parser.add_argument("--out", default="playtest",
                            help="file name prefix (default: playtest)")

    def handle(self, *args, out, **options):
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        for suffix, writer in (("games", write_games_csv), ("ratings", write_ratings_csv)):
            path = f"{out}_{suffix}.csv"
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer(f)
            self.stdout.write(f"wrote {path}")
        self.stdout.write(f"{Game.objects.count()} games, {Rating.objects.count()} ratings")
