from django.urls import path

from play import views

urlpatterns = [
    path("", views.home, name="home"),
    path("nuova/", views.create, name="create"),
    path("partita/<str:match_id>/", views.match_page, name="match"),
]
