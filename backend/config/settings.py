import os
from pathlib import Path
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.getenv("DEBUG", "0") == "1"
OWNDESK_RUNTIME = os.getenv("OWNDESK_RUNTIME", "distributed")
if OWNDESK_RUNTIME not in {"distributed", "native"}:
    raise RuntimeError("OWNDESK_RUNTIME must be distributed or native")
PRIVATE_MEDIA_ROOT = os.getenv("PRIVATE_MEDIA_ROOT", "")
DEPLOYMENT_MODE = os.getenv("DEPLOYMENT_MODE", "demo" if DEBUG else "organization")
if DEPLOYMENT_MODE not in {"demo", "organization", "portal"} or (DEPLOYMENT_MODE == "demo" and not DEBUG):
    raise RuntimeError("DEPLOYMENT_MODE must be organization or portal (demo only with DEBUG)")
if OWNDESK_RUNTIME == "native":
    media_path = Path(PRIVATE_MEDIA_ROOT)
    if DEPLOYMENT_MODE != "organization" or not PRIVATE_MEDIA_ROOT or not media_path.is_absolute():
        raise RuntimeError("Native runtime requires organization mode and an absolute PRIVATE_MEDIA_ROOT")
    if media_path.resolve().is_relative_to((BASE_DIR.parent / "frontend").resolve()):
        raise RuntimeError("PRIVATE_MEDIA_ROOT must be outside the public frontend directory")
SESSION_COOKIE_NAME = "desk_portal_session" if DEPLOYMENT_MODE == "portal" else "sessionid"
SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError("Set SECRET_KEY before starting the server")
    SECRET_KEY = "local-development-only-not-for-production"
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",")
CSRF_TRUSTED_ORIGINS = os.getenv(
    "CSRF_TRUSTED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",")
TRUST_LOOPBACK_CLIENT_IP = os.getenv("TRUST_LOOPBACK_CLIENT_IP", "0") == "1"
INSTALLED_APPS = [
    "daphne",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "channels",
    *[
        "apps." + name
        for name in [
            "accounts",
            "workspaces",
            "teams",
            "customers",
            "integrations",
            "conversations",
            "tickets",
            "sla",
            "attachments",
            "notifications",
            "audit",
        ]
    ],
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.RequestIDMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.workspaces.deployment.DeploymentBoundaryMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
ASGI_APPLICATION = "config.asgi.application"
AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "apps.accounts.backends.PublicAccountBackend",
    "django.contrib.auth.backends.ModelBackend",  # Preserve existing employee sessions.
]
database_url = os.getenv("DATABASE_URL")
if database_url:
    # ASGI request contexts must close their database connections on completion.
    # Persistent per-thread connections can outlive requests and exhaust PostgreSQL.
    DATABASES = {"default": dj_database_url.parse(database_url, conn_max_age=0)}
elif DEBUG:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.getenv("LOCAL_DB_PATH", BASE_DIR / "dev.sqlite3"),
        }
    }
else:
    raise RuntimeError("Production requires DATABASE_URL (PostgreSQL)")
if DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3":
    # Reserve the writer before an atomic block reads. DEFERRED transactions can
    # fail immediately when upgrading a read lock while another writer waits.
    DATABASES["default"].setdefault("OPTIONS", {}).update(
        transaction_mode="IMMEDIATE", timeout=20,
    )
LANGUAGE_CODE = "ru"
TIME_ZONE = "UTC"
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
CSRF_FAILURE_VIEW = "apps.core.errors.csrf_failure"
SECURE_SSL_REDIRECT = not DEBUG
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
X_FRAME_OPTIONS = "DENY"
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication"
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "EXCEPTION_HANDLER": "apps.core.errors.exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.Pagination",
    "PAGE_SIZE": 25,
}
SPECTACULAR_SETTINGS = {
    "TITLE": "OwnDesk API",
    "VERSION": "0.1.0",
    "COMPONENT_SPLIT_REQUEST": True,
}
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
    }
}
if OWNDESK_RUNTIME == "native" or (DEBUG and os.getenv("LOCAL_CHANNELS", "0") == "1"):
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
CELERY_BROKER_URL = REDIS_URL
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_ACKS_LATE = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BEAT_SCHEDULE = {
    "recover-persisted-work": {"task": "apps.integrations.tasks.sweep", "schedule": 1.0}
}
CELERY_BEAT_SCHEDULE["enforce-retention"] = {
    "task": "apps.attachments.retention.enforce_retention",
    "schedule": 3600.0,
}
TOKEN_ENCRYPTION_KEY = os.getenv("TOKEN_ENCRYPTION_KEY", "")
ACCOUNT_ENCRYPTION_KEY = os.getenv("ACCOUNT_ENCRYPTION_KEY", "")
PORTAL_RELEASE_DIR = os.getenv("PORTAL_RELEASE_DIR", "")
PORTAL_CONNECTOR_STATE_FILE = os.getenv("PORTAL_CONNECTOR_STATE_FILE", "")
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "")
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
CLOUDFLARE_ZONE_ID = os.getenv("CLOUDFLARE_ZONE_ID", "")
INSTALLATIONS_DOMAIN = os.getenv("INSTALLATIONS_DOMAIN", "owndesk.work")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")
LOCAL_TELEGRAM_POLLING = DEBUG and os.getenv("LOCAL_TELEGRAM_POLLING") == "1"
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "http://storage:9000")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "")
S3_BUCKET = os.getenv("S3_BUCKET", "service-desk")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_BYTES + 1024 * 1024
PUBLIC_REGISTRATION_ENABLED = os.getenv("PUBLIC_REGISTRATION_ENABLED", "0") == "1"
LOCAL_EMAIL_PREVIEW = DEBUG and os.getenv("LOCAL_EMAIL_PREVIEW", "0") == "1"
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "1") == "1"
EMAIL_USE_SSL = os.getenv("EMAIL_USE_SSL", "0") == "1"
EMAIL_TIMEOUT = 15
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "")
SECURE_REFERRER_POLICY = "no-referrer"
CELERY_BEAT_SCHEDULE["registration-mail"] = {
    "task": "apps.accounts.tasks.send_registration_mail", "schedule": 5.0,
}
