import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode
import httpx
import jwt
from django.conf import settings
from django.db import transaction, IntegrityError
from django.http import JsonResponse
from django.shortcuts import redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_variables
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError, PermissionDenied
from .models import User
from apps.workspaces.models import Membership
from .registration import google_enabled, public_origin, create_account, start_session

keys = jwt.PyJWKClient("https://www.googleapis.com/oauth2/v3/certs", timeout=10)


def verify_google_token(encoded, nonce):
    signing_key = keys.get_signing_key_from_jwt(encoded)
    claims = jwt.decode(encoded, signing_key.key, algorithms=["RS256"],
        audience=settings.GOOGLE_CLIENT_ID, issuer=["https://accounts.google.com", "accounts.google.com"],
        options={"require": ["exp", "iat", "sub", "iss", "aud", "nonce", "email"]})
    if (not secrets.compare_digest(str(claims.get("nonce", "")), nonce)
        or claims.get("email_verified") is not True
        or (claims.get("azp") and claims["azp"] != settings.GOOGLE_CLIENT_ID)):
        raise ValueError("Invalid Google identity")
    from django.core.validators import validate_email
    validate_email(claims["email"])
    if not claims["sub"] or len(claims["sub"]) > 255 or len(claims["email"]) > 254:
        raise ValueError("Invalid Google identity")
    return claims


@never_cache
def google_start(request):
    if not google_enabled():
        return JsonResponse({"detail": "Вход через Google ещё не настроен."}, status=503)
    state, nonce, verifier = [secrets.token_urlsafe(32) for _ in range(3)]
    request.session["google_login"] = {"state": state, "nonce": nonce, "verifier": verifier, "at": time.time()}
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return redirect("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": settings.GOOGLE_CLIENT_ID, "redirect_uri": public_origin() + "/api/v1/auth/google/callback",
        "response_type": "code", "scope": "openid email profile", "state": state, "nonce": nonce,
        "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account",
    }))


@never_cache
@sensitive_variables()
def google_callback(request):
    flow = request.session.pop("google_login", None)
    # Save consumption before network requests so a failed exchange cannot replay.
    request.session.save()
    if (not google_enabled() or not flow or time.time() - flow["at"] > 600
        or not secrets.compare_digest(request.GET.get("state", ""), flow["state"])
        or request.GET.get("error") or not request.GET.get("code")):
        return redirect("/?auth_error=google")
    try:
        response = httpx.post("https://oauth2.googleapis.com/token", data={
            "client_id": settings.GOOGLE_CLIENT_ID, "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "code": request.GET["code"], "grant_type": "authorization_code",
            "redirect_uri": public_origin() + "/api/v1/auth/google/callback", "code_verifier": flow["verifier"],
        }, timeout=15)
        response.raise_for_status()
        claims = verify_google_token(response.json()["id_token"], flow["nonce"])
        with transaction.atomic():
            user = User.objects.select_for_update().filter(google_sub=claims["sub"]).first()
            if user:
                membership = Membership.objects.filter(user=user, active=True).first()
                if not user.is_active or (not membership and settings.DEPLOYMENT_MODE != "portal"):
                    return redirect("/?auth_error=access")
            else:
                if not settings.PUBLIC_REGISTRATION_ENABLED:
                    return redirect("/?auth_error=registration_disabled")
                # Email equality alone never links a Google identity to an existing account.
                if User.objects.filter(email__iexact=claims["email"]).exists():
                    return redirect("/?auth_error=existing_account")
                user, membership = create_account(claims["email"].lower(), str(claims.get("name") or "Пользователь")[:150], google_sub=claims["sub"])
        from .security import begin_login
        if not begin_login(request,user,membership if settings.DEPLOYMENT_MODE!="portal" else None):
            return redirect("/?mfa=1")
        return redirect("/" if settings.DEPLOYMENT_MODE == "portal" else "/tickets")
    except (httpx.HTTPError, jwt.PyJWTError, ValueError, KeyError, IntegrityError, DjangoValidationError, ValidationError, PermissionDenied):
        return redirect("/?auth_error=google")
