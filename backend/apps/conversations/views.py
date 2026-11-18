from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.generics import ListAPIView
from rest_framework.exceptions import NotFound
from apps.core.access import member, tickets_for, team_ids
from apps.tickets.serializers import MessageSerializer
from .models import Conversation, Message


class ChatMessagesView(ListAPIView):
    serializer_class = MessageSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Message.objects.none()
        m = member(self.request)
        chat = get_object_or_404(
            Conversation, id=self.kwargs["pk"], workspace=m.workspace, approved=True
        )
        tickets = tickets_for(m).filter(conversation=chat)
        group_access = m.role == "admin" or (
            chat.connection_id and chat.connection.default_team_id in team_ids(m)
        )
        if not group_access and not tickets.exists():
            raise NotFound()
        permitted = Q(ticket__in=tickets)
        if group_access:
            permitted |= Q(ticket=None)
        return (
            Message.objects.filter(
                permitted, conversation=chat, workspace=m.workspace, visibility="public"
            )
            .select_related("job")
            .prefetch_related("attachments")
            .order_by("-created_at", "-id")
        )
