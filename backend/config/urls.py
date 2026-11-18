from django.urls import path, include
from rest_framework.routers import DefaultRouter
from drf_spectacular.views import SpectacularAPIView
from django.http import HttpResponse
from apps.accounts.views import AuthView, CSRFView, MeView, LogoutView
from apps.accounts.registration import RegistrationOptions, Register, VerifyRegistration
from apps.accounts.google import google_start, google_callback
from apps.accounts.security import AccountSecurity, VerifySecondFactor, RequestPasswordRecovery, ResetPassword
from apps.tickets.views import TicketViewSet
from apps.core.resources import (
    CustomerViewSet,
    CompanyViewSet,
    TeamViewSet,
    EmployeeViewSet,
    TemplateViewSet,
    FilterViewSet,
    PolicyViewSet,
)
from apps.core.reports import ClosuresView
from apps.core.views import (
    CountsView,
    QueueOptionsView,
    NotificationsView,
    NotificationReadView,
    ReportsView,
    HealthView,
    LiveView,
    OperationsView,
)
from apps.integrations.views import (
    ConnectionsView,
    ConnectionHealth,
    WebhookView,
    GroupsView,
    UntriagedView,
    TriageView,
)
from apps.attachments.views import UploadView, DownloadView
from apps.workspaces.views import WorkspaceSettingsView, EscalationRouteView
from apps.workspaces.invitations import InvitationsView, RevokeInvitation, InspectInvitation, AcceptInvitation
from apps.workspaces.ownership import TransferOwnership
from apps.workspaces.setup import CompleteSetup
from apps.workspaces.connection import OrganizationConnection
from apps.workspaces.connector_access import ConnectorConfiguration, ConnectorActivation, ConnectorProof
from apps.workspaces.releases import PortalRelease, PortalReleaseDownload
from apps.workspaces.installations import (
    InstallationsView, RevokeInstallation, PairInstallationView, InstallationHeartbeat,
)
from apps.conversations.views import ChatMessagesView
from apps.customers.support import (
    CustomerSupportLink, SupportSession, SupportTickets, SupportMessages, SupportDownload,
)

router = DefaultRouter(trailing_slash=False)
for route, view, name in [
    ("tickets", TicketViewSet, "ticket"),
    ("customers", CustomerViewSet, "customer"),
    ("companies", CompanyViewSet, "company"),
    ("teams", TeamViewSet, "team"),
    ("employees", EmployeeViewSet, "employee"),
    ("templates", TemplateViewSet, "template"),
    ("filters", FilterViewSet, "filter"),
    ("sla-policies", PolicyViewSet, "sla"),
]:
    router.register(route, view, basename=name)
urlpatterns = [
    path(
        "api/v1/",
        include(
            [
                path("", include(router.urls)),
                path("auth/csrf", CSRFView.as_view()),
                path("auth/login", AuthView.as_view()),
                path("auth/options", RegistrationOptions.as_view()),
                path("auth/security", AccountSecurity.as_view()),
                path("auth/mfa", VerifySecondFactor.as_view()),
                path("auth/recovery", RequestPasswordRecovery.as_view()),
                path("auth/reset", ResetPassword.as_view()),
                path("portal/release", PortalRelease.as_view()),
                path("portal/release/download", PortalReleaseDownload.as_view()),
                path("portal/installations", InstallationsView.as_view()),
                path("portal/installations/pair", PairInstallationView.as_view()),
                path("portal/installations/<uuid:pk>/heartbeat", InstallationHeartbeat.as_view()),
                path("portal/installations/<uuid:pk>/configuration", ConnectorConfiguration.as_view()),
                path("portal/installations/<uuid:pk>/activate", ConnectorActivation.as_view()),
                path("connector/proof", ConnectorProof.as_view()),
                path("portal/installations/<uuid:pk>/revoke", RevokeInstallation.as_view()),
                path("customers/<uuid:customer_id>/support-link", CustomerSupportLink.as_view()),
                path("support/session", SupportSession.as_view()),
                path("support/tickets", SupportTickets.as_view()),
                path("support/tickets/<uuid:ticket_id>/messages", SupportMessages.as_view()),
                path("support/attachments/<uuid:attachment_id>", SupportDownload.as_view()),
                path("setup/complete", CompleteSetup.as_view()),
                path("workspace/portal-connection", OrganizationConnection.as_view()),
                path("invitations", InvitationsView.as_view()),
                path("invitations/<uuid:pk>/revoke", RevokeInvitation.as_view()),
                path("auth/invitations/inspect", InspectInvitation.as_view()),
                path("auth/invitations/accept", AcceptInvitation.as_view()),
                path("auth/register", Register.as_view()),
                path("auth/verify", VerifyRegistration.as_view()),
                path("auth/google/start", google_start),
                path("auth/google/callback", google_callback),
                path("auth/me", MeView.as_view()),
                path("auth/logout", LogoutView.as_view()),
                path("queues/counts", CountsView.as_view()),
                path("integrations/telegram", ConnectionsView.as_view()),
                path("integrations/<uuid:pk>/health", ConnectionHealth.as_view()),
                path("integrations/groups", GroupsView.as_view()),
                path("webhooks/telegram/<uuid:connection_id>", WebhookView.as_view()),
                path("untriaged", UntriagedView.as_view()),
                path("untriaged/<uuid:pk>/assign", TriageView.as_view()),
                path("attachments", UploadView.as_view()),
                path("attachments/<uuid:pk>/download", DownloadView.as_view()),
                path("notifications", NotificationsView.as_view()),
                path("notifications/<uuid:pk>/read", NotificationReadView.as_view()),
                path("reports/summary", ReportsView.as_view()),
                path("reports/closures", ClosuresView.as_view()),
                path("health", HealthView.as_view()),
                path("operations", OperationsView.as_view()),
                path("workspace/transfer-ownership", TransferOwnership.as_view()),
                path("workspace/escalation-route", EscalationRouteView.as_view()),
                path("workspace/settings", WorkspaceSettingsView.as_view()),
                path("health/live", LiveView.as_view()),
                path("health/ready", HealthView.as_view()),
                path("queues/options", QueueOptionsView.as_view()),
                path("conversations/<uuid:pk>/messages", ChatMessagesView.as_view()),
                path("schema", SpectacularAPIView.as_view(), name="schema"),
                path("docs", lambda request: HttpResponse('<!doctype html><html lang="ru"><meta charset="utf-8"><title>OwnDesk API</title><main><h1>OwnDesk API</h1><p>Версия API: v1. Авторизация использует серверную сессию и CSRF cookie.</p><p><a href="/api/v1/schema">Открыть схему OpenAPI</a></p><p>Начните с GET /api/v1/auth/csrf, затем вызовите POST /api/v1/auth/login. Список доступных обращений возвращает GET /api/v1/tickets.</p></main></html>')),
            ]
        ),
    )
]

# A local review can use the packaged frontend without Node or a reverse proxy.
from django.conf import settings

if settings.DEBUG:
    from django.urls import re_path
    from django.views.static import serve

    dist = settings.BASE_DIR.parent / "frontend" / "dist"
    urlpatterns += [
        path("assets/<path:path>", serve, {"document_root": dist / "assets"}),
        re_path(
            r"^(?!api/|ws/|assets/).*$",
            serve,
            {"document_root": dist, "path": "index.html"},
        ),
    ]
