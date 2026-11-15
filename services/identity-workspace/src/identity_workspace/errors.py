"""Contract error envelopes; never reflect exception messages or input values."""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import Field
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from identity_workspace.correlation import request_id_context
from identity_workspace.validation import ContractModel

DEFAULT_ERRORS = {
    400: ("MALFORMED_REQUEST", "The request is malformed."),
    401: ("AUTH_REQUIRED", "Authentication is required."),
    403: ("FORBIDDEN", "This operation is not permitted."),
    404: ("NOT_FOUND", "The requested resource is unavailable."),
    405: ("METHOD_NOT_ALLOWED", "The method is not allowed for this route."),
    409: (None, "The request conflicts with the current state."),
    412: ("VERSION_CONFLICT", "The resource version has changed."),
    413: ("PAYLOAD_TOO_LARGE", "The request body exceeds 64 KiB."),
    415: ("UNSUPPORTED_MEDIA_TYPE", "A UTF-8 JSON request body is required."),
    422: ("VALIDATION_FAILED", "The request contains invalid fields."),
    428: ("PRECONDITION_REQUIRED", "A required precondition is missing."),
    429: ("RATE_LIMITED", "Too many requests. Retry later."),
    500: ("INTERNAL_ERROR", "An internal error occurred."),
    503: ("DEPENDENCY_UNAVAILABLE", "A required dependency is unavailable."),
}

BUSINESS_CODES = {
    400: {"MALFORMED_REQUEST", "INVALID_CURSOR", "IDEMPOTENCY_KEY_REQUIRED"},
    401: {"AUTH_REQUIRED", "SESSION_INVALID", "GOOGLE_TOKEN_INVALID", "AUTH_CHALLENGE_INVALID", "SERVICE_CREDENTIAL_INVALID"},
    403: {"FORBIDDEN", "ORIGIN_NOT_ALLOWED"},
    404: {"NOT_FOUND", "USER_NOT_FOUND"},
    409: {"WORKSPACE_LIMIT_REACHED", "BOARD_LIMIT_REACHED", "MEMBER_LIMIT_REACHED", "ALREADY_MEMBER", "INVITATION_ALREADY_PENDING", "INVITATION_NOT_PENDING", "OWNER_CANNOT_LEAVE", "IDEMPOTENCY_CONFLICT", "IDEMPOTENCY_IN_PROGRESS", "EMAIL_AMBIGUOUS"},
}


class ErrorDetail(ContractModel):
    field: str = Field(min_length=1, max_length=150)
    reason: str = Field(min_length=1, max_length=200)


class ErrorContent(ContractModel):
    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=500)
    request_id: str
    details: list[ErrorDetail]


class ErrorEnvelope(ContractModel):
    error: ErrorContent


class APIError(Exception):
    """An intentional public error; supply only controlled, non-sensitive text."""

    def __init__(
        self, status_code: int, *, code: str | None = None,
        message: str | None = None, details: list[ErrorDetail] | None = None,
        retry_after: int | None = None,
    ):
        default_code, default_message = DEFAULT_ERRORS[status_code]
        self.status_code = status_code
        self.code = code or default_code
        if self.code is None or self.code not in BUSINESS_CODES.get(status_code, {default_code}):
            raise ValueError("An explicit contract error code is required for this status.")
        self.message = message or default_message
        self.details = details or []
        self.retry_after = retry_after
        super().__init__(self.message)


def error_response(error: APIError, *, headers: dict[str, str] | None = None) -> JSONResponse:
    body = ErrorEnvelope(error=ErrorContent(
        code=error.code, message=error.message,
        request_id=request_id_context.get() or "00000000-0000-4000-8000-000000000000",
        details=error.details,
    ))
    response_headers = dict(headers or {})
    if error.status_code in {429, 503}:
        response_headers["Retry-After"] = str(max(1, min(error.retry_after or 2, 86400)))
    return JSONResponse(body.model_dump(), status_code=error.status_code, headers=response_headers)


def validation_details(errors: list[dict], declared_fields: set[str]) -> list[ErrorDetail]:
    reasons = {
        "missing": "This field is required.",
        "extra_forbidden": "Unknown properties are not allowed.",
        "string_too_short": "Below the permitted text length.",
        "string_too_long": "Exceeds the permitted text length.",
        "string_type": "Must be text.",
        "int_type": "Must be an integer.",
        "int_parsing": "Must be an integer.",
        "bool_type": "Must be a boolean.",
        "bool_parsing": "Must be a boolean.",
        "finite_number": "Must be a finite number.",
        "model_attributes_type": "Must be a JSON object.",
    }
    details = []
    for item in errors[:50]:
        kind = item.get("type", "")
        location = tuple(item.get("loc", ()))
        field_location = location[1:] if location[:1] in {("body",), ("query",), ("path",), ("header",), ("cookie",)} else location
        # Unknown property/dictionary keys are user-controlled and may be secrets.
        names = [] if kind == "extra_forbidden" else [
            part for part in field_location if isinstance(part, str) and part in declared_fields
        ]
        field = ".".join(names)[:150] or ("query" if location[:1] == ("query",) else "body")
        details.append(ErrorDetail(
            field=field,
            reason=reasons.get(kind, "Does not satisfy this field's validation rules."),
        ))
    return details


async def api_error_handler(request: Request, error: APIError):
    return error_response(error)


async def validation_error_handler(request: Request, error: RequestValidationError):
    errors = error.errors()
    if any(item.get("type") == "json_invalid" for item in errors):
        return error_response(APIError(400))
    declared = getattr(request.state, "declared_fields", set())
    return error_response(APIError(422, details=validation_details(errors, declared)))


async def http_error_handler(request: Request, error: HTTPException):
    status = error.status_code if error.status_code in DEFAULT_ERRORS else 500
    if status == 409:
        # A bare HTTPException cannot identify the correct domain conflict code.
        status = 500
    # Framework exception detail may contain an upstream error or submitted value.
    headers = {}
    if status == 405 and error.headers and "Allow" in error.headers:
        methods = {value.strip() for value in error.headers["Allow"].split(",")}
        if methods and methods <= {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
            headers["Allow"] = ", ".join(sorted(methods))
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    retry_after = None
    if status in {429, 503} and error.headers:
        value = error.headers.get("Retry-After", "")
        if value.isascii() and value.isdecimal() and len(value) <= 5:
            retry_after = int(value)
    return error_response(APIError(status, retry_after=retry_after), headers=headers)
