"""OpenAPI contracts for APIViews that do not use a ModelSerializer."""

from drf_spectacular.utils import extend_schema, extend_schema_view

STRING = {"type": "string"}
ID = {"type": "string", "format": "uuid"}
INT = {"type": "integer"}
BOOL = {"type": "boolean"}
JSON = {"type": "object", "additionalProperties": True}


def obj(**fields):
    return {"type": "object", "properties": fields}


def array(item):
    return {"type": "array", "items": item}


def results(item):
    return obj(results=array(item))


OK = obj(ok=BOOL)
BOT = obj(
    id=ID,
    username=STRING,
    bot_id=STRING,
    state=STRING,
    last_error=STRING,
    default_team_id=ID,
)
ME = obj(
    deployment_mode=STRING,
    is_owner=BOOL,
    id=ID,
    membership_id=ID,
    username=STRING,
    name=STRING,
    role=STRING,
    workspace=obj(id=ID, name=STRING, timezone=STRING),
    preferences=JSON,
    development=BOOL,
)
GROUP = obj(id=ID, title=STRING, external_id=STRING, approved=BOOL, mode=STRING)

CONTRACTS = {
    "RegistrationOptions": {"get": (None, obj(email_registration=BOOL, google_login=BOOL, public_registration=BOOL, local_email_preview=BOOL, deployment_mode=STRING, setup_required=BOOL))},
    "Register": {"post": (obj(name=STRING, email=STRING), obj(message=STRING, preview_link={"type": "string", "nullable": True}))},
    "VerifyRegistration": {"post": (obj(token=STRING, password={"type": "string", "format": "password"}), OK)},
    "AuthView": {
        "post": (
            obj(username=STRING, password={"type": "string", "format": "password"}),
            OK,
        )
    },
    "CSRFView": {"get": (None, obj(csrf=STRING))},
    "LogoutView": {"post": (None, OK)},
    "MeView": {
        "get": (None, ME),
        "patch": (obj(tickets_expanded=BOOL, theme=STRING, columns=array(STRING)), ME),
    },
    "HealthView": {"get": (None, obj(status=STRING))},
    "LiveView": {"get": (None, obj(status=STRING))},
    "CountsView": {
        "get": (
            None,
            obj(active=INT, mine=INT, free=INT, overdue=INT, all=INT, untriaged=INT),
        )
    },
    "QueueOptionsView": {
        "get": (
            None,
            obj(
                customer=array(obj(id=ID, name=STRING)),
                company=array(obj(id=ID, name=STRING)),
                connection=array(obj(id=ID, name=STRING)),
            ),
        )
    },
    "OperationsView": {
        "get": (
            None,
            obj(
                worker=STRING,
                worker_seen_at=STRING,
                pending_events=INT,
                outbox=array(obj(state=STRING, count=INT)),
            ),
        )
    },
    "ConnectionsView": {
        "get": (None, results(BOT)),
        "post": (obj(token={"type": "string", "writeOnly": True}, team_id=ID), BOT),
    },
    "ConnectionHealth": {
        "get": (
            None,
            obj(
                id=ID,
                state=STRING,
                pending_events=INT,
                telegram_pending=INT,
                webhook_configured=BOOL,
                error=STRING,
            ),
        ),
        "patch": (
            obj(enabled=BOOL, token={"type": "string", "writeOnly": True}),
            obj(id=ID, state=STRING),
        ),
    },
    "GroupsView": {
        "get": (None, results(GROUP)),
        "patch": (obj(id=ID, approved=BOOL, mode=STRING), OK),
    },
    "WebhookView": {
        "post": (
            obj(update_id=INT, message=JSON, edited_message=JSON, callback_query=JSON),
            OK,
        )
    },
    "TriageView": {"post": (obj(ticket_id=ID, subject=STRING), obj(ticket_id=ID))},
    "UntriagedView": {
        "get": (
            None,
            obj(
                count=INT,
                next=STRING,
                previous=STRING,
                results=array({"$ref": "#/components/schemas/Message"}),
            ),
        )
    },
    "NotificationsView": {
        "get": (
            None,
            results(
                obj(
                    id=ID,
                    kind=STRING,
                    text=STRING,
                    ticket_id=ID,
                    read_at=STRING,
                    created_at=STRING,
                )
            ),
        )
    },
    "NotificationReadView": {"post": (None, OK)},
    "ReportsView": {
        "get": (
            None,
            obj(
                date_basis=STRING,
                unit=STRING,
                created=INT,
                resolved=INT,
                closed=INT,
                active_now=INT,
                free_now=INT,
                without_response=INT,
                reopened=INT,
                first_response_median={"type": "number", "nullable": True},
                first_response_p90={"type": "number", "nullable": True},
                resolution_median={"type": "number", "nullable": True},
                sla_percent={"type": "number", "nullable": True},
                load=array(JSON),
            ),
        )
    },
    "ClosuresView": {
        "get": (
            None,
            obj(**{
                "from": STRING, "to": STRING, "timezone": STRING, "hourly": BOOL, "total": INT,
                "employees": array(obj(id={**ID, "nullable": True}, name=STRING, count=INT)),
                "buckets": array(obj(key=STRING, count=INT)),
                "details": obj(count=INT, page_size=INT, results=array(obj(
                    id=ID, number=INT, subject=STRING, closed_at=STRING,
                    employee_id={**ID, "nullable": True},
                ))),
            }),
        ),
    },
    "UploadView": {
        "post": (
            {"multipart/form-data": obj(file={"type": "string", "format": "binary"})},
            {"$ref": "#/components/schemas/Attachment"},
        )
    },
    "DownloadView": {"get": (None, {"type": "string", "format": "binary"})},
}


def documented(view):
    operations = {}
    for method, (request, response) in CONTRACTS[view.__name__].items():
        code = (
            201
            if view.__name__ in ["UploadView", "ConnectionsView"] and method == "post"
            else 200
        )
        if view.__name__ == "Register":
            code = 202
        if request and "type" in request:
            request = {"application/json": request}
        operations[method] = extend_schema(request=request, responses={code: response})
    return extend_schema_view(**operations)(view)
