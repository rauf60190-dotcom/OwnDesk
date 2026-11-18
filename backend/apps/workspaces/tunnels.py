"""Portal-only Cloudflare provisioning. Prepared routes return 404 until activation."""
import re

import httpx
from django.conf import settings
from django.db import transaction

from .models import ConnectedInstallation, InstallationRoute


class TunnelError(Exception):
    pass


class Cloudflare:
    def __init__(self):
        self.account = settings.CLOUDFLARE_ACCOUNT_ID
        self.zone = settings.CLOUDFLARE_ZONE_ID
        token = settings.CLOUDFLARE_API_TOKEN
        if not token or not all(re.fullmatch(r"[a-fA-F0-9]{32}", value) for value in (self.account, self.zone)):
            raise TunnelError("Настройте серверные реквизиты Cloudflare на портале.")
        self.client = httpx.Client(
            base_url="https://api.cloudflare.com/client/v4/", timeout=15, follow_redirects=False,
            headers={"Authorization": "Bearer " + token},
        )

    def close(self):
        self.client.close()

    def request(self, method, path, **kwargs):
        try:
            response = self.client.request(method, path, **kwargs)
            if method == "DELETE" and response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()
            if data.get("success") is not True:
                raise ValueError("Cloudflare rejected request")
            return data["result"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            # Never log provider response bodies or authentication headers.
            raise TunnelError("Cloudflare не завершил операцию. Повторите попытку; созданные ресурсы будут использованы повторно.") from None

    def tunnel_path(self, route):
        return f"accounts/{self.account}/cfd_tunnel/{route.tunnel_id}"

    def block(self, route):
        if route.tunnel_id:
            self.request("PUT", self.tunnel_path(route) + "/configurations", json={
                "config": {"ingress": [{"service": "http_status:404"}]},
            })

    def prepare(self, route):
        if route.state in {"checking", "active"}:
            return
        name = "owndesk-" + str(route.installation_id)
        if not route.tunnel_id:
            collection = f"accounts/{self.account}/cfd_tunnel"
            existing = self.request("GET", collection, params={"name": name, "is_deleted": "false"})
            matches = [item for item in existing if item.get("name") == name and not item.get("deleted_at")]
            if len(matches) > 1:
                raise TunnelError("Найдено несколько туннелей установки. Требуется проверка администратора портала.")
            tunnel = matches[0] if matches else self.request("POST", collection, json={
                "name": name, "config_src": "cloudflare",
            })
            if tunnel.get("config_src") == "local" or tunnel.get("remote_config") is False:
                raise TunnelError("Имя установки занято локально управляемым туннелем.")
            route.tunnel_id = tunnel["id"]
            route.save()
        # Preparing an address must never publish a development server.
        self.block(route)
        collection = f"zones/{self.zone}/dns_records"
        records = self.request("GET", collection, params={"name": route.hostname})
        content = f"{route.tunnel_id}.cfargotunnel.com"
        if records:
            if len(records) != 1 or any(
                record.get("type") != "CNAME" or record.get("content") != content
                or not record.get("proxied") or record.get("name") != route.hostname for record in records
            ):
                raise TunnelError("DNS-имя уже занято другой записью. Существующая запись не изменена.")
            record = records[0]
        else:
            record = self.request("POST", collection, json={
                "type": "CNAME", "proxied": True, "name": route.hostname, "content": content,
            })
        route.dns_record_id = record["id"]
        route.state = "prepared"
        route.save()

    def configure(self, route, *, full=False):
        rule = {"hostname": route.hostname, "service": "http://127.0.0.1:5173"}
        if not full:
            rule["path"] = "^/api/v1/connector/proof$"
        self.request("PUT", self.tunnel_path(route) + "/configurations", json={
            "config": {"ingress": [rule, {"service": "http_status:404"}]},
        })

    def token(self, route):
        token = self.request("GET", self.tunnel_path(route) + "/token")
        if not isinstance(token, str) or not 32 <= len(token) <= 16384 or not re.fullmatch(r"[A-Za-z0-9+/=_-]+", token):
            raise TunnelError("Cloudflare вернул недействительный токен туннеля.")
        return token

    def revoke(self, route):
        self.block(route)
        if route.dns_record_id:
            self.request("DELETE", f"zones/{self.zone}/dns_records/{route.dns_record_id}")
        route.state = "revoked"
        route.save()


def prepare_route(installation_id):
    if settings.DEPLOYMENT_MODE != "portal":
        raise TunnelError("Создание маршрутов доступно только на портале.")
    domain = settings.INSTALLATIONS_DOMAIN
    if len(domain) > 215 or any(len(label) > 63 for label in domain.split(".")) or not re.fullmatch(
        r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+", domain,
    ):
        raise TunnelError("Проверьте домен установок на портале.")
    error = None
    provider = Cloudflare()
    try:
        with transaction.atomic():
            item = ConnectedInstallation.objects.select_for_update().get(pk=installation_id)
            if not item.paired_at or item.revoked_at or not item.owner.is_active:
                raise TunnelError("Сначала привяжите активную установку к порталу.")
            route, _ = InstallationRoute.objects.get_or_create(installation=item, defaults={
                "hostname": "org-" + item.pk.hex + "." + domain,
            })
            if route.state == "revoked":
                raise TunnelError("Маршрут отозван. Создайте новую привязку установки.")
            try:
                provider.prepare(route)
            except TunnelError as exc:
                # Commit checkpoints even if DNS failed after tunnel creation.
                route.state = "error"
                route.save()
                error = exc
        if error:
            raise error
        return route
    finally:
        provider.close()


def revoke_route(item):
    """Caller holds the installation row lock; deny confirmation if provider failed."""
    route = InstallationRoute.objects.filter(installation=item).first()
    if not route or route.state == "revoked":
        return
    provider = Cloudflare()
    try:
        provider.revoke(route)
    finally:
        provider.close()
