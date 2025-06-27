import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Games.settings')

from django.core.asgi import get_asgi_application

asgi_application = get_asgi_application()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter

from . import routing

application = ProtocolTypeRouter(
    {
        'http': asgi_application,
        'websocket': AuthMiddlewareStack(URLRouter(routing.websocket_urlpatterns)),
    }
)
