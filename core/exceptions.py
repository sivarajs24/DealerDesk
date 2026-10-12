from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status
from django.http import Http404
from rest_framework.exceptions import (
    NotFound,
    ValidationError,
    MethodNotAllowed,
    ParseError,
    APIException,
)


def api_error(code: str, message: str, details=None, status_code: int = 400) -> Response:
    return Response(
        {
            "error": {
                "code": code,
                "message": message,
                "details": details if details is not None else [],
            }
        },
        status=status_code,
    )


def custom_exception_handler(exc, context):
    # Call REST framework's default exception handler first,
    # to get the standard error response.
    response = exception_handler(exc, context)

    if isinstance(exc, (Http404, NotFound)):
        return api_error("NOT_FOUND", "The requested resource was not found.", status_code=404)

    if isinstance(exc, ValidationError):
        details = []
        message = "Validation error"
        if isinstance(exc.detail, dict):
            for field, errs in exc.detail.items():
                if isinstance(errs, list):
                    for err in errs:
                        details.append(f"{field}: {err}")
                else:
                    details.append(f"{field}: {errs}")
            if details:
                message = details[0]
        elif isinstance(exc.detail, list):
            details = [str(e) for e in exc.detail]
            if details:
                message = details[0]
        else:
            message = str(exc.detail)
            details = [message]

        return api_error("VALIDATION_ERROR", message, details=details, status_code=400)

    if response is not None:
        code = "VALIDATION_ERROR" if response.status_code == 400 else "ERROR"
        if response.status_code == 404:
            code = "NOT_FOUND"
        elif response.status_code == 409:
            code = "CONFLICT"

        message = str(response.data) if hasattr(response, "data") else "An error occurred."
        return api_error(code, message, status_code=response.status_code)

    return response
