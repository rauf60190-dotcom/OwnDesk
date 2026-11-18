import asyncio
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async
from apps.workspaces.models import Membership
from apps.core.access import tickets_for


class DeskConsumer(AsyncJsonWebsocketConsumer):
    @database_sync_to_async
    def allowed(self, ticket_id=None):
        from django.contrib.sessions.models import Session
        from django.utils import timezone

        if not Session.objects.filter(
            session_key=self.scope["session"].session_key,
            expire_date__gt=timezone.now(),
        ).exists():
            return False
        m = Membership.objects.filter(
            user_id=self.scope["user"].pk,
            user__is_active=True,
            active=True,
            workspace_id=self.scope["session"].get("workspace_id"),
        ).first()
        return bool(
            m and (not ticket_id or tickets_for(m).filter(pk=ticket_id).exists())
        )

    async def connect(self):
        from django.conf import settings
        if settings.DEPLOYMENT_MODE == "portal":
            await self.close(code=4403)
            return
        if not self.scope["user"].is_authenticated or not await self.allowed():
            await self.close(code=4403)
            return
        self.group = f"user_{self.scope['user'].id.hex}"
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        self.guard = asyncio.create_task(self.check_access())

    async def check_access(self):
        while True:
            await asyncio.sleep(10)
            if not await self.allowed():
                await self.close(code=4403)
                return

    async def disconnect(self, code):
        if hasattr(self, "guard"):
            self.guard.cancel()
        if hasattr(self, "group"):
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def desk_event(self, event):
        if await self.allowed(event["data"].get("object_id")):
            await self.send_json(event["data"])
        elif not await self.allowed():
            await self.close(code=4403)
