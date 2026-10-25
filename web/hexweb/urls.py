from django.urls import path
from django.views.generic import RedirectView

from play import views

urlpatterns = [
    path("", views.home, name="home"),
    path("new/", views.create, name="create"),
    path("game/<str:match_id>/", views.match_page, name="match"),
    path("registry/", views.registry, name="registry"),
    path("registry/games.csv", views.registry_games_csv, name="registry_games_csv"),
    path("registry/ratings.csv", views.registry_ratings_csv, name="registry_ratings_csv"),
    path("registry/<str:match_id>.json", views.registry_game_json, name="registry_game"),
    # indirizzi della versione italiana: i link già mandati agli amici continuano a funzionare
    path("partita/<str:match_id>/", RedirectView.as_view(pattern_name="match", permanent=True)),
    path("registro/", RedirectView.as_view(pattern_name="registry", permanent=True)),
]
