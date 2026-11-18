import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
from django.core.asgi import get_asgi_application

django_asgi = get_asgi_application()
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from channels.security.websocket import AllowedHostsOriginValidator
from django.urls import path
from apps.notifications.consumers import DeskConsumer

protocol_application = ProtocolTypeRouter(
    {
        "http": django_asgi,
        "websocket": AllowedHostsOriginValidator(
            AuthMiddlewareStack(URLRouter([path("ws/events", DeskConsumer.as_asgi())]))
        ),
    }
)


async def application(scope, receive, send):
    from django.conf import settings
    if settings.OWNDESK_RUNTIME == "native":
        from apps.core.native_events import bind_loop
        bind_loop()
    await protocol_application(scope, receive, send)
