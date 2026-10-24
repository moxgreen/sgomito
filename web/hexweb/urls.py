from django.urls import path

from play import views

urlpatterns = [
    path("", views.home, name="home"),
    path("nuova/", views.create, name="create"),
    path("partita/<str:match_id>/", views.match_page, name="match"),
    path("registro/", views.registry, name="registry"),
    path("registro/partite.csv", views.registry_games_csv, name="registry_games_csv"),
    path("registro/giudizi.csv", views.registry_ratings_csv, name="registry_ratings_csv"),
    path("registro/<str:match_id>.json", views.registry_game_json, name="registry_game"),
]
