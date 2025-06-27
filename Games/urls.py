from django.urls import path, re_path, include, reverse_lazy
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.views.generic.base import RedirectView

from . import views

urlpatterns = [
    path('nations/', include('Nations.urls')),
    path('accounts/credentials/', include('users.urls')),
    path('', views.home, name='home'),
    path('accounts/signup/', views.sign_up, name='signup'),
    path('accounts/logout/', views.log_out, name='logout'),
    path('accounts/', include('django.contrib.auth.urls')),
    path('accounts/profile/', views.profile, name='profile'),
    path('admin/', admin.site.urls),
    re_path(r'^static/protected/(?P<path>.*)$', views.protected_static, name='protected_static'),
]
