from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler as drf_handler


def csrf_failure(request, reason=""):
    from django.http import JsonResponse

    return JsonResponse(
        {
            "code": "csrf_failed",
            "message": "Обновите страницу и повторите действие.",
            "fields": {},
            "request_id": getattr(request, "request_id", ""),
        },
        status=403,
    )


class Conflict(APIException):
    status_code = 409
    default_detail = "Обращение изменилось. Обновите карточку и повторите действие."
    default_code = "conflict"

    def __init__(self, detail=None, current=None):
        self.current = current
        super().__init__(detail)


def exception_handler(exc, context):
    from django.core.exceptions import ValidationError as DjangoValidationError
    from rest_framework.exceptions import ValidationError

    if isinstance(exc, DjangoValidationError):
        exc = ValidationError("Неверные данные запроса.")
    response = drf_handler(exc, context)
    if response is not None:
        detail = response.data
        response.data = {
            "code": getattr(exc, "default_code", "error"),
            "message": str(detail.get("detail", "Проверьте заполненные поля."))
            if isinstance(detail, dict)
            else str(detail),
            "fields": detail
            if isinstance(detail, dict) and "detail" not in detail
            else {},
            "request_id": getattr(context["request"], "request_id", ""),
        }
        if isinstance(exc, Conflict) and exc.current is not None:
            response.data["current"] = exc.current
    return response
