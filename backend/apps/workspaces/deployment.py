from django.conf import settings
from django.http import JsonResponse
from django.urls import Resolver404, resolve


class DeploymentBoundaryMiddleware:
    """Portal accounts cannot reach service-desk routes, even with a stale session."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.DEPLOYMENT_MODE == "portal" and request.path.startswith("/api/"):
            allowed = {"/api/v1/auth/csrf", "/api/v1/auth/login", "/api/v1/auth/logout", "/api/v1/auth/me",
                "/api/v1/auth/security", "/api/v1/auth/mfa", "/api/v1/auth/recovery", "/api/v1/auth/reset",
                "/api/v1/portal/release", "/api/v1/portal/release/download",
                "/api/v1/auth/options", "/api/v1/auth/register", "/api/v1/auth/verify",
                "/api/v1/auth/google/start", "/api/v1/auth/google/callback", "/api/v1/health/live", "/api/v1/health/ready"}
            installation_routes = {
                "InstallationsView", "RevokeInstallation", "PairInstallationView", "InstallationHeartbeat",
                "ConnectorConfiguration", "ConnectorActivation",
            }
            installation_view = ""
            if request.path.startswith("/api/v1/portal/installations"):
                try:
                    installation_view = getattr(resolve(request.path).func, "view_class", type(None)).__name__
                except Resolver404:
                    pass
            if request.path not in allowed and installation_view not in installation_routes:
                return JsonResponse({"message":"Рабочие данные доступны только на сервере организации."}, status=404)
        return self.get_response(request)
