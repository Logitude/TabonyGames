from django.urls import re_path

from . import consumers
import Nations.consumers

websocket_urlpatterns = [
    re_path(r'ws/$', consumers.GamesConsumer.as_asgi()),
    re_path(r'ws/nations/$', Nations.consumers.NationsConsumer.as_asgi()),
    re_path(r'ws/nations/(?P<match_id>\d+)/$', Nations.consumers.NationsMatchConsumer.as_asgi()),
]
