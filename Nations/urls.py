from django.urls import path

from . import views

app_name = 'Nations'
urlpatterns = [
    path('', views.home, name='home'),
    path('create/', views.create_match, name='create'),
    path('matches/', views.matches, name='matches'),
    path('matches/open/', views.open_matches, name='open_matches'),
    path('matches/completed/', views.completed_matches, name='completed_matches'),
    path('matches/mine/', views.my_matches, name='my_matches'),
    path('archive/', views.archive, name='archive'),
    path('archive/open/', views.archive_open, name='archive_open'),
    path('archive/ongoing/', views.archive_ongoing, name='archive_ongoing'),
    path('archive/completed/', views.archive_completed, name='archive_completed'),
    path('archive/mine/', views.archive_mine, name='archive_mine'),
    path('<int:pk>/', views.match, name='match'),
]
