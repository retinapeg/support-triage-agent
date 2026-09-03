"""Deterministic tools used by the Support Triage Agent.

The project deliberately uses a small, inspectable fixture set.  That keeps the
demo runnable without credentials while preserving the same structured boundary
that a production log, status-page, documentation, or ticketing adapter would
use.  Nothing in this module performs fuzzy record lookup: an unknown identifier
is returned as unknown rather than being filled in by inference.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Callable, Mapping

try:  # Supports both ``python demo.py`` and package-style imports in tests.
    from .models import Evidence, ToolResult
except ImportError:  # pragma: no cover - exercised by the normal CLI entrypoint.
    from models import Evidence, ToolResult


FIXTURE_OBSERVED_AT = "2026-09-03T09:30:00Z"


# These are intentionally synthetic, non-secret records.  Request bodies and API
# key values are not stored; only fields that are useful for support diagnosis
# are exposed.
_API_REQUESTS: dict[str, dict[str, Any]] = {
    "req_ok_200": {
        "request_id": "req_ok_200",
        "occurred_at": "2026-09-03T08:41:12Z",
        "method": "POST",
        "path": "/v1/evaluations",
        "status_code": 200,
        "latency_ms": 184,
        "api_key_id": "key_valid",
        "region": "eu-west",
        "outcome": "completed",
        "details": {"response_shape": "evaluation", "validation_errors": []},
    },
    "req_400_invalid": {
        "request_id": "req_400_invalid",
        "occurred_at": "2026-09-03T08:02:14Z",
        "method": "POST",
        "path": "/v1/applicants",
        "status_code": 400,
        "latency_ms": 31,
        "api_key_id": "key_valid",
        "region": "eu-west",
        "outcome": "rejected",
        "error": {
            "code": "invalid_request",
            "message": "Required field 'name' was absent.",
            "field": "name",
        },
        "details": {"content_type": "application/json", "body_logged": False},
    },
    "req_401_expired": {
        "request_id": "req_401_expired",
        "occurred_at": "2026-09-03T08:16:09Z",
        "method": "GET",
        "path": "/v1/evaluations/ev_demo_4821",
        "status_code": 401,
        "latency_ms": 18,
        "api_key_id": "key_expired",
        "region": "eu-west",
        "outcome": "rejected",
        "error": {
            "code": "credential_expired",
            "message": "The presented credential had expired.",
        },
        "details": {"authentication_scheme": "Bearer", "credential_logged": False},
    },
    "req_401_revoked": {
        "request_id": "req_401_revoked",
        "occurred_at": "2026-09-03T08:18:42Z",
        "method": "POST",
        "path": "/v1/evaluations",
        "status_code": 401,
        "latency_ms": 16,
        "api_key_id": "key_revoked",
        "region": "eu-west",
        "outcome": "rejected",
        "error": {
            "code": "credential_revoked",
            "message": "The presented credential had been revoked.",
        },
        "details": {"authentication_scheme": "Bearer", "credential_logged": False},
    },
    "req_403_scope": {
        "request_id": "req_403_scope",
        "occurred_at": "2026-09-03T08:24:03Z",
        "method": "GET",
        "path": "/v1/reports/rpt_demo_204",
        "status_code": 403,
        "latency_ms": 22,
        "api_key_id": "key_missing_scope",
        "region": "eu-west",
        "outcome": "rejected",
        "error": {
            "code": "insufficient_scope",
            "message": "Credential does not grant the required permission.",
            "required_scope": "reports:read",
        },
        "details": {"credential_logged": False},
    },
    "req_409_duplicate": {
        "request_id": "req_409_duplicate",
        "occurred_at": "2026-09-03T08:29:55Z",
        "method": "POST",
        "path": "/v1/applicants",
        "status_code": 409,
        "latency_ms": 47,
        "api_key_id": "key_valid",
        "region": "eu-west",
        "outcome": "rejected",
        "error": {
            "code": "idempotency_conflict",
            "message": "The idempotency key was reused with different parameters.",
        },
        "details": {
            "idempotency_key_present": True,
            "idempotency_key_logged": False,
            "existing_resource_id": "app_demo_712",
        },
    },
    "req_412_stale": {
        "request_id": "req_412_stale",
        "occurred_at": "2026-09-03T08:34:27Z",
        "method": "PATCH",
        "path": "/v1/applicants/app_demo_712",
        "status_code": 412,
        "latency_ms": 39,
        "api_key_id": "key_valid",
        "region": "eu-west",
        "outcome": "rejected",
        "error": {
            "code": "precondition_failed",
            "message": "The supplied resource version did not match the current version.",
        },
        "details": {"supplied_version": "7", "current_version": "8"},
    },
    "req_429_burst": {
        "request_id": "req_429_burst",
        "occurred_at": "2026-09-03T08:46:51Z",
        "method": "POST",
        "path": "/v1/evaluations",
        "status_code": 429,
        "latency_ms": 14,
        "api_key_id": "key_valid",
        "region": "eu-west",
        "outcome": "rate_limited",
        "error": {
            "code": "rate_limit_exceeded",
            "message": "The per-minute request limit was exceeded.",
        },
        "details": {
            "limit": 120,
            "window_seconds": 60,
            "requests_observed": 137,
            "retry_after_seconds": 18,
        },
    },
    "req_500_internal": {
        "request_id": "req_500_internal",
        "occurred_at": "2026-09-03T08:52:19Z",
        "method": "POST",
        "path": "/v1/evaluations",
        "status_code": 500,
        "latency_ms": 2187,
        "api_key_id": "key_valid",
        "region": "eu-west",
        "outcome": "failed",
        "error": {
            "code": "internal_error",
            "message": "The service could not complete the request.",
        },
        "details": {
            "trace_id": "trace_demo_500_a17",
            "failure_stage": "rules_evaluation",
            "request_body_logged": False,
        },
    },
    "req_502_upstream": {
        "request_id": "req_502_upstream",
        "occurred_at": "2026-09-03T08:54:06Z",
        "method": "GET",
        "path": "/v1/data-sources/source_demo_9",
        "status_code": 502,
        "latency_ms": 5021,
        "api_key_id": "key_valid",
        "region": "us-east",
        "outcome": "failed",
        "error": {
            "code": "upstream_unavailable",
            "message": "A dependent service did not return a valid response.",
        },
        "details": {
            "trace_id": "trace_demo_502_b41",
            "dependency": "data-enrichment",
            "retry_succeeded": False,
        },
    },
    "req_503_unavailable": {
        "request_id": "req_503_unavailable",
        "occurred_at": "2026-09-03T08:56:44Z",
        "method": "POST",
        "path": "/v1/evaluations",
        "status_code": 503,
        "latency_ms": 63,
        "api_key_id": "key_valid",
        "region": "us-east",
        "outcome": "failed",
        "error": {
            "code": "temporarily_unavailable",
            "message": "Capacity was temporarily unavailable.",
        },
        "details": {"trace_id": "trace_demo_503_c08", "retry_after_seconds": 30},
    },
}


_AUTHENTICATION_RECORDS: dict[str, dict[str, Any]] = {
    "key_valid": {
        "api_key_id": "key_valid",
        "status": "active",
        "environment": "production",
        "created_at": "2026-06-10T12:00:00Z",
        "expires_at": "2027-06-10T12:00:00Z",
        "revoked_at": None,
        "scopes": ["evaluations:read", "evaluations:write", "reports:read"],
        "last_success_at": "2026-09-03T09:21:17Z",
        "secret_material_returned": False,
    },
    "key_expiring": {
        "api_key_id": "key_expiring",
        "status": "active",
        "environment": "production",
        "created_at": "2025-09-04T08:00:00Z",
        "expires_at": "2026-09-04T08:00:00Z",
        "revoked_at": None,
        "scopes": ["evaluations:read", "evaluations:write"],
        "last_success_at": "2026-09-03T09:18:02Z",
        "warnings": ["Credential expires in less than 24 hours."],
        "secret_material_returned": False,
    },
    "key_expired": {
        "api_key_id": "key_expired",
        "status": "expired",
        "environment": "production",
        "created_at": "2025-09-03T08:00:00Z",
        "expires_at": "2026-09-03T08:00:00Z",
        "revoked_at": None,
        "scopes": ["evaluations:read", "evaluations:write"],
        "last_success_at": "2026-09-03T07:58:47Z",
        "secret_material_returned": False,
    },
    "key_revoked": {
        "api_key_id": "key_revoked",
        "status": "revoked",
        "environment": "production",
        "created_at": "2026-01-15T10:00:00Z",
        "expires_at": "2027-01-15T10:00:00Z",
        "revoked_at": "2026-09-03T08:10:00Z",
        "scopes": ["evaluations:read", "evaluations:write"],
        "last_success_at": "2026-09-03T08:09:41Z",
        "secret_material_returned": False,
    },
    "key_missing_scope": {
        "api_key_id": "key_missing_scope",
        "status": "active",
        "environment": "production",
        "created_at": "2026-08-20T15:00:00Z",
        "expires_at": "2027-08-20T15:00:00Z",
        "revoked_at": None,
        "scopes": ["evaluations:read"],
        "last_success_at": "2026-09-03T09:12:54Z",
        "secret_material_returned": False,
    },
    "key_test_environment": {
        "api_key_id": "key_test_environment",
        "status": "active",
        "environment": "sandbox",
        "created_at": "2026-07-01T11:30:00Z",
        "expires_at": None,
        "revoked_at": None,
        "scopes": ["evaluations:read", "evaluations:write"],
        "last_success_at": "2026-09-03T09:03:15Z",
        "warnings": ["This credential is valid only for the sandbox environment."],
        "secret_material_returned": False,
    },
}


_WEBHOOK_DELIVERIES: dict[str, dict[str, Any]] = {
    "evt_delivered": {
        "event_id": "evt_delivered",
        "event_type": "evaluation.completed",
        "created_at": "2026-09-03T08:12:00Z",
        "delivery_created": True,
        "delivery_status": "delivered",
        "failure_type": None,
        "subscription": {
            "subscription_id": "whsub_primary",
            "enabled": True,
            "event_type_match": True,
            "endpoint": "https://hooks.customer.example/evaluations",
        },
        "attempts": [
            {
                "attempt": 1,
                "delivery_id": "whdel_demo_001",
                "attempted_at": "2026-09-03T08:12:02Z",
                "response_status": 204,
                "duration_ms": 142,
            }
        ],
        "payload_logged": False,
    },
    "evt_endpoint_500": {
        "event_id": "evt_endpoint_500",
        "event_type": "evaluation.completed",
        "created_at": "2026-09-03T08:20:00Z",
        "delivery_created": True,
        "delivery_status": "failed",
        "failure_type": "endpoint_response",
        "subscription": {
            "subscription_id": "whsub_primary",
            "enabled": True,
            "event_type_match": True,
            "endpoint": "https://hooks.customer.example/evaluations",
        },
        "attempts": [
            {
                "attempt": 1,
                "delivery_id": "whdel_demo_101",
                "attempted_at": "2026-09-03T08:20:02Z",
                "response_status": 500,
                "duration_ms": 391,
            },
            {
                "attempt": 2,
                "delivery_id": "whdel_demo_102",
                "attempted_at": "2026-09-03T08:21:02Z",
                "response_status": 502,
                "duration_ms": 644,
            },
            {
                "attempt": 3,
                "delivery_id": "whdel_demo_103",
                "attempted_at": "2026-09-03T08:23:02Z",
                "response_status": 503,
                "duration_ms": 522,
            },
        ],
        "payload_logged": False,
    },
    "evt_endpoint_timeout": {
        "event_id": "evt_endpoint_timeout",
        "event_type": "applicant.updated",
        "created_at": "2026-09-03T08:31:00Z",
        "delivery_created": True,
        "delivery_status": "failed",
        "failure_type": "endpoint_timeout",
        "subscription": {
            "subscription_id": "whsub_updates",
            "enabled": True,
            "event_type_match": True,
            "endpoint": "https://hooks.customer.example/applicants",
        },
        "attempts": [
            {
                "attempt": 1,
                "delivery_id": "whdel_demo_201",
                "attempted_at": "2026-09-03T08:31:02Z",
                "response_status": None,
                "network_error": "connect_timeout",
                "duration_ms": 5000,
            },
            {
                "attempt": 2,
                "delivery_id": "whdel_demo_202",
                "attempted_at": "2026-09-03T08:32:02Z",
                "response_status": None,
                "network_error": "connect_timeout",
                "duration_ms": 5000,
            },
        ],
        "payload_logged": False,
    },
    "evt_signature_401": {
        "event_id": "evt_signature_401",
        "event_type": "evaluation.completed",
        "created_at": "2026-09-03T08:39:00Z",
        "delivery_created": True,
        "delivery_status": "failed",
        "failure_type": "endpoint_response",
        "subscription": {
            "subscription_id": "whsub_primary",
            "enabled": True,
            "event_type_match": True,
            "endpoint": "https://hooks.customer.example/evaluations",
        },
        "attempts": [
            {
                "attempt": 1,
                "delivery_id": "whdel_demo_301",
                "attempted_at": "2026-09-03T08:39:02Z",
                "response_status": 401,
                "duration_ms": 88,
            },
            {
                "attempt": 2,
                "delivery_id": "whdel_demo_302",
                "attempted_at": "2026-09-03T08:40:02Z",
                "response_status": 401,
                "duration_ms": 91,
            },
        ],
        "payload_logged": False,
        "diagnostic_note": (
            "The receiver returned 401. A signing-secret mismatch is a hypothesis, "
            "not confirmed by these delivery records."
        ),
    },
    "evt_subscription_disabled": {
        "event_id": "evt_subscription_disabled",
        "event_type": "evaluation.completed",
        "created_at": "2026-09-03T08:44:00Z",
        "delivery_created": False,
        "delivery_status": "not_attempted",
        "failure_type": "configuration",
        "subscription": {
            "subscription_id": "whsub_primary",
            "enabled": False,
            "disabled_at": "2026-09-03T08:00:00Z",
            "event_type_match": True,
            "endpoint": "https://hooks.customer.example/evaluations",
        },
        "attempts": [],
        "payload_logged": False,
    },
    "evt_no_subscription": {
        "event_id": "evt_no_subscription",
        "event_type": "data_source.alerted",
        "created_at": "2026-09-03T08:49:00Z",
        "delivery_created": False,
        "delivery_status": "not_attempted",
        "failure_type": "configuration",
        "subscription": {
            "subscription_id": None,
            "enabled": None,
            "event_type_match": False,
            "endpoint": None,
        },
        "attempts": [],
        "payload_logged": False,
        "diagnostic_note": "No enabled subscription matched this event type at creation time.",
    },
    "evt_endpoint_gone": {
        "event_id": "evt_endpoint_gone",
        "event_type": "applicant.created",
        "created_at": "2026-09-03T08:58:00Z",
        "delivery_created": True,
        "delivery_status": "failed",
        "failure_type": "endpoint_response",
        "subscription": {
            "subscription_id": "whsub_legacy",
            "enabled": True,
            "event_type_match": True,
            "endpoint": "https://hooks.customer.example/legacy",
        },
        "attempts": [
            {
                "attempt": 1,
                "delivery_id": "whdel_demo_401",
                "attempted_at": "2026-09-03T08:58:02Z",
                "response_status": 410,
                "duration_ms": 77,
            }
        ],
        "payload_logged": False,
    },
}


_HTTP_STATUSES: dict[int, dict[str, Any]] = {
    200: {
        "name": "OK",
        "class": "success",
        "meaning": "The request completed successfully.",
        "usually_retryable": False,
    },
    202: {
        "name": "Accepted",
        "class": "success",
        "meaning": "The request was accepted for asynchronous processing.",
        "usually_retryable": False,
    },
    204: {
        "name": "No Content",
        "class": "success",
        "meaning": "The request completed and intentionally returned no body.",
        "usually_retryable": False,
    },
    400: {
        "name": "Bad Request",
        "class": "client_error",
        "meaning": "The server rejected the request syntax or validation.",
        "usually_retryable": False,
        "next_check": "Inspect validation errors, content type, and required fields.",
    },
    401: {
        "name": "Unauthorized",
        "class": "client_error",
        "meaning": "The request lacks valid authentication credentials.",
        "usually_retryable": False,
        "next_check": "Check credential status, expiry, environment, and auth scheme.",
    },
    403: {
        "name": "Forbidden",
        "class": "client_error",
        "meaning": "The server understood the request but will not authorize it.",
        "usually_retryable": False,
        "next_check": "Check scopes, roles, resource access, and environment policy.",
    },
    404: {
        "name": "Not Found",
        "class": "client_error",
        "meaning": "The requested resource was not found.",
        "usually_retryable": False,
        "next_check": "Verify the identifier, path, environment, and resource lifecycle.",
    },
    409: {
        "name": "Conflict",
        "class": "client_error",
        "meaning": "The request conflicts with the resource's current state.",
        "usually_retryable": "after_state_refresh",
        "next_check": "Inspect duplicate, idempotency, or concurrent-update details.",
    },
    410: {
        "name": "Gone",
        "class": "client_error",
        "meaning": "The target resource is no longer available.",
        "usually_retryable": False,
        "next_check": "Replace or reconfigure the retired target.",
    },
    412: {
        "name": "Precondition Failed",
        "class": "client_error",
        "meaning": "A request precondition did not match current server state.",
        "usually_retryable": "after_state_refresh",
        "next_check": "Fetch current state or version, then retry with a fresh precondition.",
    },
    422: {
        "name": "Unprocessable Content",
        "class": "client_error",
        "meaning": "The request was understood but failed semantic validation.",
        "usually_retryable": False,
        "next_check": "Correct the field-level validation errors before retrying.",
    },
    429: {
        "name": "Too Many Requests",
        "class": "client_error",
        "meaning": "The caller exceeded an enforced request rate.",
        "usually_retryable": True,
        "next_check": "Honor Retry-After and use bounded exponential backoff with jitter.",
    },
    500: {
        "name": "Internal Server Error",
        "class": "server_error",
        "meaning": "The server encountered an unexpected condition.",
        "usually_retryable": True,
        "next_check": "Preserve request and trace IDs; retry safely, then escalate repeats.",
    },
    502: {
        "name": "Bad Gateway",
        "class": "server_error",
        "meaning": "A gateway received an invalid response from an upstream service.",
        "usually_retryable": True,
        "next_check": "Check service status and retry safely; escalate persistent failures.",
    },
    503: {
        "name": "Service Unavailable",
        "class": "server_error",
        "meaning": "The service is temporarily unable to handle the request.",
        "usually_retryable": True,
        "next_check": "Honor Retry-After, check status, and retry with backoff.",
    },
    504: {
        "name": "Gateway Timeout",
        "class": "server_error",
        "meaning": "A gateway did not receive a timely upstream response.",
        "usually_retryable": True,
        "next_check": "Check status and retry idempotently with bounded backoff.",
    },
}


_INTERNAL_DOCS: tuple[dict[str, Any], ...] = (
    {
        "document_id": "doc_auth_401",
        "title": "Diagnosing 401 authentication failures",
        "keywords": {"401", "authentication", "credential", "token", "expired", "revoked", "api key"},
        "summary": (
            "Compare the failed request's key ID with credential status, expiry, "
            "environment, and Authorization scheme. Never request the secret value."
        ),
    },
    {
        "document_id": "doc_permissions_403",
        "title": "Diagnosing 403 permission failures",
        "keywords": {"403", "forbidden", "permission", "scope", "role"},
        "summary": (
            "Confirm the credential is valid, then compare its scopes and resource "
            "access with the permission required by the endpoint."
        ),
    },
    {
        "document_id": "doc_requests_400",
        "title": "Request validation and 400 responses",
        "keywords": {"400", "bad request", "validation", "payload", "field", "content type"},
        "summary": (
            "Use the structured validation response to check required fields, data "
            "types, JSON encoding, and Content-Type."
        ),
    },
    {
        "document_id": "doc_state_conflicts",
        "title": "Resolving 409 and 412 state conflicts",
        "keywords": {"409", "412", "conflict", "idempotency", "precondition", "version", "state"},
        "summary": (
            "For 409, inspect duplicate or idempotency context. For 412, refresh the "
            "resource version before a guarded retry."
        ),
    },
    {
        "document_id": "doc_rate_limits",
        "title": "Handling API rate limits",
        "keywords": {"429", "rate limit", "retry-after", "backoff", "throttle"},
        "summary": (
            "Honor Retry-After, cap concurrency, and apply exponential backoff with "
            "jitter. Avoid immediate unbounded retries."
        ),
    },
    {
        "document_id": "doc_server_errors",
        "title": "Triage for persistent 5xx responses",
        "keywords": {"500", "502", "503", "504", "5xx", "server error", "trace", "outage"},
        "summary": (
            "Capture timestamps, request and trace IDs, region, retry outcome, and "
            "customer impact. Escalate repeated failures with this evidence."
        ),
    },
    {
        "document_id": "doc_webhooks",
        "title": "Webhook delivery troubleshooting",
        "keywords": {"webhook", "delivery", "event", "endpoint", "subscription", "signature", "timeout"},
        "summary": (
            "Start with an event ID and timestamp. Distinguish no matching subscription "
            "from a delivery attempt rejected or timed out by the receiver."
        ),
    },
)


def _unknown_result(tool_name: str, identifier_name: str, identifier: Any) -> ToolResult:
    """Return an explicit miss without manufacturing a substitute record."""

    return ToolResult(
        tool_name=tool_name,
        success=False,
        summary=f"No mocked record matched {identifier_name}={identifier!r}.",
        data={"found": False, identifier_name: identifier},
        evidence=[],
        error=f"unknown_{identifier_name}",
    )


def inspect_api_request(request_id: str) -> ToolResult:
    """Inspect one API-gateway request by its exact request ID."""

    normalized = request_id.strip() if isinstance(request_id, str) else request_id
    record = _API_REQUESTS.get(normalized)
    if record is None:
        return _unknown_result("inspect_api_request", "request_id", normalized)

    status_code = record["status_code"]
    error_code = record.get("error", {}).get("code")
    auth_result = {
        "credential_expired": "expired",
        "credential_revoked": "revoked",
        "insufficient_scope": "missing_scope",
    }.get(error_code)
    return ToolResult(
        tool_name="inspect_api_request",
        success=True,
        summary=(
            f"Found {record['method']} {record['path']}: HTTP {status_code} "
            f"({record['outcome']})."
        ),
        data={
            "found": True,
            # Frequently used decision fields are duplicated at the envelope
            # level; the complete immutable-style record remains under request.
            "status_code": status_code,
            "auth_result": auth_result,
            "request": deepcopy(record),
        },
        evidence=[
            Evidence(
                source="mock_api_gateway_logs",
                fact=(
                    f"Request {normalized} returned HTTP {status_code} at "
                    f"{record['occurred_at']}."
                ),
                observed_at=record["occurred_at"],
                identifiers={"request_id": normalized, "http_status": str(status_code)},
            )
        ],
        error=None,
    )


def check_authentication(api_key_id: str) -> ToolResult:
    """Return metadata for an API key ID; secret key material is never returned."""

    normalized = api_key_id.strip() if isinstance(api_key_id, str) else api_key_id
    record = _AUTHENTICATION_RECORDS.get(normalized)
    if record is None:
        return _unknown_result("check_authentication", "api_key_id", normalized)

    status = record["status"]
    diagnostic_status = (
        "missing_scope"
        if normalized == "key_missing_scope"
        else {"active": "valid"}.get(status, status)
    )
    return ToolResult(
        tool_name="check_authentication",
        success=True,
        summary=(
            f"Credential metadata found: status={status}, "
            f"environment={record['environment']}; secret material was not returned."
        ),
        data={
            "found": True,
            "status": diagnostic_status,
            "environment": record["environment"],
            "authentication": deepcopy(record),
        },
        evidence=[
            Evidence(
                source="mock_credential_registry",
                fact=f"Credential {normalized} was {status} when inspected.",
                observed_at=FIXTURE_OBSERVED_AT,
                identifiers={"api_key_id": normalized},
            )
        ],
        error=None,
    )


def _webhook_outcome(record: Mapping[str, Any]) -> tuple[str, int | None]:
    """Normalise raw delivery/configuration evidence for the decision adapter."""

    response_codes = [
        attempt.get("response_status")
        for attempt in record.get("attempts", [])
        if attempt.get("response_status") is not None
    ]
    response_code = response_codes[-1] if response_codes else None
    if record.get("delivery_status") == "delivered":
        return "delivered", response_code
    if record.get("failure_type") == "endpoint_timeout":
        return "endpoint_timeout", response_code
    if record.get("failure_type") == "configuration":
        subscription = record.get("subscription", {})
        if subscription.get("subscription_id") is None:
            return "no_subscription", response_code
        if subscription.get("enabled") is False:
            return "subscription_disabled", response_code
    if response_code == 401:
        # The log proves only that the receiver rejected the delivery. A
        # signing-secret mismatch is one hypothesis, not an observed fact.
        return "endpoint_401", response_code
    if response_code is not None and 500 <= response_code <= 599:
        return "endpoint_5xx", response_code
    if response_code == 410:
        return "endpoint_gone", response_code
    return "inconclusive", response_code


def inspect_webhook_delivery(event_id: str) -> ToolResult:
    """Inspect event routing, subscription configuration, and delivery attempts."""

    normalized = event_id.strip() if isinstance(event_id, str) else event_id
    record = _WEBHOOK_DELIVERIES.get(normalized)
    if record is None:
        return _unknown_result("inspect_webhook_delivery", "event_id", normalized)

    if record["delivery_created"]:
        fact = (
            f"Event {normalized} has {len(record['attempts'])} delivery attempt(s); "
            f"final state is {record['delivery_status']}."
        )
    else:
        fact = (
            f"Event {normalized} produced no delivery attempt; configuration state "
            f"was {record['failure_type']}."
        )

    outcome, response_code = _webhook_outcome(record)
    return ToolResult(
        tool_name="inspect_webhook_delivery",
        success=True,
        summary=fact,
        data={
            "found": True,
            "outcome": outcome,
            "response_code": response_code,
            "webhook_delivery": deepcopy(record),
        },
        evidence=[
            Evidence(
                source="mock_webhook_delivery_logs",
                fact=fact,
                observed_at=record["created_at"],
                identifiers={"event_id": normalized},
            )
        ],
        error=None,
    )


def check_service_status() -> ToolResult:
    """Return a deterministic status-page snapshot for the demo environment."""

    status = {
        "found": True,
        "observed_at": FIXTURE_OBSERVED_AT,
        "overall_status": "operational",
        "components": {
            "public_api": "operational",
            "authentication": "operational",
            "webhook_dispatch": "operational",
            "data_enrichment": "operational",
        },
        "active_incidents": [],
        "scope_note": (
            "A green aggregate status does not rule out a request-specific or "
            "customer-endpoint failure."
        ),
    }
    return ToolResult(
        tool_name="check_service_status",
        success=True,
        summary="All mocked service components are operational; no active incident is listed.",
        data=status,
        evidence=[
            Evidence(
                source="mock_status_page",
                fact="No active platform incident was listed in the status snapshot.",
                observed_at=FIXTURE_OBSERVED_AT,
                identifiers={},
            )
        ],
        error=None,
    )


def lookup_http_status(code: int | str) -> ToolResult:
    """Look up support-oriented guidance for a known HTTP status code."""

    try:
        normalized = int(code)
    except (TypeError, ValueError):
        return ToolResult(
            tool_name="lookup_http_status",
            success=False,
            summary=f"HTTP status code {code!r} is not a valid integer.",
            data={"found": False, "code": code},
            evidence=[],
            error="invalid_http_status_code",
        )

    record = _HTTP_STATUSES.get(normalized)
    if record is None:
        return _unknown_result("lookup_http_status", "code", normalized)

    payload = {"code": normalized, **deepcopy(record)}
    return ToolResult(
        tool_name="lookup_http_status",
        success=True,
        summary=f"HTTP {normalized} means {record['name']}: {record['meaning']}",
        data={"found": True, "status": payload},
        evidence=[
            Evidence(
                source="mock_http_reference",
                fact=f"HTTP {normalized} is classified as {record['class']} ({record['name']}).",
                observed_at=None,
                identifiers={"http_status": str(normalized)},
            )
        ],
        error=None,
    )


def search_internal_docs(query: str) -> ToolResult:
    """Search a fixed support-document corpus using transparent keyword matching."""

    normalized = " ".join(query.lower().split()) if isinstance(query, str) else ""
    if not normalized:
        return ToolResult(
            tool_name="search_internal_docs",
            success=False,
            summary="A non-empty documentation query is required.",
            data={"found": False, "query": query, "results": []},
            evidence=[],
            error="empty_query",
        )

    query_tokens = set(normalized.replace("/", " ").replace("-", " ").split())
    ranked: list[tuple[int, dict[str, Any]]] = []
    for document in _INTERNAL_DOCS:
        searchable_phrases = document["keywords"]
        score = sum(
            1
            for phrase in searchable_phrases
            if phrase in normalized or phrase in query_tokens
        )
        if score:
            ranked.append((score, document))

    ranked.sort(key=lambda item: (-item[0], item[1]["document_id"]))
    results = [
        {
            "document_id": document["document_id"],
            "title": document["title"],
            "summary": document["summary"],
            "match_score": score,
        }
        for score, document in ranked[:3]
    ]

    if not results:
        return ToolResult(
            tool_name="search_internal_docs",
            success=True,
            summary="The documentation search completed, but no mocked document matched.",
            data={"found": False, "query": query, "results": []},
            evidence=[],
            error=None,
        )

    return ToolResult(
        tool_name="search_internal_docs",
        success=True,
        summary=f"Found {len(results)} relevant mocked support document(s).",
        data={"found": True, "query": query, "results": results},
        evidence=[
            Evidence(
                source="mock_internal_documentation",
                fact=f"Documentation search matched {result['document_id']}.",
                observed_at=None,
                identifiers={"document_id": result["document_id"]},
            )
            for result in results
        ],
        error=None,
    )


def _plain_value(value: Any) -> Any:
    """Convert Pydantic/dataclass values to JSON-friendly built-ins."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return _plain_value(value.value)
    if isinstance(value, Mapping):
        return {str(key): _plain_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain_value(item) for item in value]
    if is_dataclass(value):
        return _plain_value(asdict(value))

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(mode="json")
        except TypeError:  # Pydantic v1-like compatibility.
            return model_dump()

    dict_method = getattr(value, "dict", None)
    if callable(dict_method):
        return _plain_value(dict_method())

    return value


def _case_state_dict(case_state: Any) -> dict[str, Any] | None:
    """Read a dict, Pydantic model, dataclass, or CaseState-like object."""

    converted = _plain_value(case_state)
    if isinstance(converted, Mapping):
        return dict(converted)

    field_names = (
        "case_id",
        "original_message",
        "customer",
        "issue_category",
        "severity",
        "customer_impact",
        "escalation_reason",
        "timestamps",
        "request_ids",
        "http_codes",
        "webhook_ids",
        "api_key_ids",
        "endpoints",
        "evidence",
        "actions_taken",
        "tool_results",
        "missing_information",
        "hypotheses",
        "next_action",
        "escalation_required",
        "resolution",
    )
    state = {
        field_name: _plain_value(getattr(case_state, field_name))
        for field_name in field_names
        if hasattr(case_state, field_name)
    }
    return state or None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


def _hypothesis_text(candidate: Any) -> str:
    """Render a case-supplied hypothesis without adding a new conclusion."""

    plain = _plain_value(candidate)
    if isinstance(plain, Mapping):
        statement = plain.get("statement") or plain.get("hypothesis")
        if statement:
            return str(statement)
    return str(plain)


def _likely_root_cause(hypotheses: Any) -> str:
    """Select a stated hypothesis without promoting it to confirmed evidence."""

    candidates = _as_list(hypotheses)
    if not candidates:
        return "Undetermined from available evidence."

    # CaseState may use strings or richer hypothesis objects. Preserve the first
    # state-supplied candidate verbatim and label its epistemic status clearly.
    return f"Unconfirmed hypothesis: {_hypothesis_text(candidates[0])}"


def _action_summaries(actions: Any) -> list[str]:
    """Turn state action records into the strings required by the handoff model."""

    summaries: list[str] = []
    for action in _as_list(actions):
        plain = _plain_value(action)
        if isinstance(plain, Mapping):
            summary = plain.get("summary") or plain.get("action")
            tool_name = plain.get("tool_name")
            if summary:
                rendered = str(summary)
                if tool_name:
                    rendered = f"{rendered} (tool: {tool_name})"
                summaries.append(rendered)
        elif plain is not None:
            summaries.append(str(plain))
    return summaries


def _tool_observation_summaries(results: Any) -> list[dict[str, Any]]:
    """Keep escalation evidence useful without copying entire mock log records."""

    summaries: list[dict[str, Any]] = []
    for result in _as_list(results):
        plain = _plain_value(result)
        if not isinstance(plain, Mapping):
            continue
        tool_name = str(plain.get("tool_name") or "unknown_tool")
        data = plain.get("data") if isinstance(plain.get("data"), Mapping) else {}
        details: dict[str, Any] = {}

        if tool_name == "check_service_status":
            details = {
                "overall_status": data.get("overall_status"),
                "active_incidents": data.get("active_incidents", []),
                "observed_at": data.get("observed_at"),
            }
        elif tool_name == "inspect_api_request":
            request = data.get("request") if isinstance(data.get("request"), Mapping) else {}
            request_details = request.get("details") if isinstance(request.get("details"), Mapping) else {}
            details = {
                "request_id": request.get("request_id"),
                "occurred_at": request.get("occurred_at"),
                "path": request.get("path"),
                "status_code": request.get("status_code"),
                "region": request.get("region"),
                "error": request.get("error"),
                "trace_id": request_details.get("trace_id"),
                "failure_stage": request_details.get("failure_stage"),
            }
        elif tool_name == "check_authentication":
            auth = data.get("authentication") if isinstance(data.get("authentication"), Mapping) else {}
            details = {
                "api_key_id": auth.get("api_key_id"),
                "status": data.get("status"),
                "environment": data.get("environment"),
                "expires_at": auth.get("expires_at"),
                "scopes": auth.get("scopes", []),
            }
        elif tool_name == "inspect_webhook_delivery":
            delivery = (
                data.get("webhook_delivery")
                if isinstance(data.get("webhook_delivery"), Mapping)
                else {}
            )
            attempts = []
            for attempt in _as_list(delivery.get("attempts")):
                if isinstance(attempt, Mapping):
                    attempts.append(
                        {
                            "attempted_at": attempt.get("attempted_at"),
                            "response_status": attempt.get("response_status"),
                            "network_error": attempt.get("network_error"),
                        }
                    )
            details = {
                "event_id": delivery.get("event_id"),
                "event_type": delivery.get("event_type"),
                "outcome": data.get("outcome"),
                "failure_type": delivery.get("failure_type"),
                "response_code": data.get("response_code"),
                "attempts": attempts,
            }
        elif tool_name == "lookup_http_status":
            status = data.get("status") if isinstance(data.get("status"), Mapping) else {}
            details = {
                "code": status.get("code"),
                "class": status.get("class"),
                "usually_retryable": status.get("usually_retryable"),
                "next_check": status.get("next_check"),
            }
        elif tool_name == "search_internal_docs":
            details = {
                "document_ids": [
                    item.get("document_id")
                    for item in _as_list(data.get("results"))
                    if isinstance(item, Mapping)
                ]
            }

        # Remove absent optional values while preserving false, zero, and empty
        # collections where those values are meaningful observations.
        details = {key: value for key, value in details.items() if value is not None}
        summaries.append(
            {
                "tool_name": tool_name,
                "success": bool(plain.get("success")),
                "summary": str(plain.get("summary") or ""),
                "details": details,
            }
        )
    return summaries


def create_engineering_escalation(case_state: Any) -> ToolResult:
    """Build an engineering handoff strictly from accumulated case state."""

    state = _case_state_dict(case_state)
    if state is None:
        return ToolResult(
            tool_name="create_engineering_escalation",
            success=False,
            summary="case_state must be a mapping or a CaseState-like object.",
            data={"found": False, "escalation": None},
            evidence=[],
            error="invalid_case_state",
        )

    case_id = state.get("case_id")
    original_message = state.get("original_message")
    customer_impact = state.get("customer_impact")
    evidence = _as_list(state.get("evidence"))
    actions = _action_summaries(state.get("actions_taken"))
    missing = [str(item) for item in _as_list(state.get("missing_information"))]

    if not case_id:
        return ToolResult(
            tool_name="create_engineering_escalation",
            success=False,
            summary="Cannot create a traceable escalation without case_id.",
            data={"found": False, "escalation": None},
            evidence=[],
            error="missing_case_id",
        )

    escalation = {
        "case_id": str(case_id),
        "customer": state.get("customer") or "Unknown (not provided in case state)",
        "severity": _plain_value(state.get("severity")) or "medium",
        "escalation_reason": state.get("escalation_reason") or (
            "Support triage could not safely confirm a resolution from available evidence."
        ),
        "customer_impact": (
            customer_impact
            if customer_impact
            else "Not established from available case evidence."
        ),
        "symptoms": [original_message] if original_message else [],
        "timestamps": _as_list(state.get("timestamps")),
        "request_ids": _as_list(state.get("request_ids")),
        "http_status_codes": _as_list(state.get("http_codes")),
        "webhook_ids": _as_list(state.get("webhook_ids")),
        "relevant_details": {
            "evidence": evidence,
            "api_key_ids": _as_list(state.get("api_key_ids")),
            "endpoints": _as_list(state.get("endpoints")),
            "tool_observations": _tool_observation_summaries(state.get("tool_results")),
        },
        "troubleshooting_performed": actions,
        "likely_root_cause": _likely_root_cause(state.get("hypotheses")),
        "outstanding_questions": missing,
    }

    return ToolResult(
        tool_name="create_engineering_escalation",
        success=True,
        summary=(
            f"Created engineering escalation for case {case_id}."
        ),
        data={"found": True, "escalation": escalation},
        evidence=[
            Evidence(
                source="engineering_escalation_builder",
                fact="Escalation generated from supplied case state without adding customer facts.",
                observed_at=None,
                identifiers={"case_id": str(case_id)},
            )
        ],
        error=None,
    )


# OpenAI-compatible function metadata.  The LLM adapter can pass this list to a
# provider and dispatch the selected name through ``dispatch_tool`` below.
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "inspect_api_request",
            "description": (
                "Inspect a single API request using an exact customer-supplied request ID. "
                "Do not call until a request ID is known."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "request_id": {
                        "type": "string",
                        "description": "Exact API request ID supplied by the customer or case evidence.",
                    }
                },
                "required": ["request_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_authentication",
            "description": (
                "Check status, expiry, environment, and scopes for an exact API key ID. "
                "Never ask for or pass secret key material."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "api_key_id": {
                        "type": "string",
                        "description": "Non-secret API key identifier, not the API key value.",
                    }
                },
                "required": ["api_key_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_webhook_delivery",
            "description": (
                "Inspect the exact webhook event, matching subscription, endpoint, and delivery attempts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "Exact webhook event ID supplied by the customer or case evidence.",
                    }
                },
                "required": ["event_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_service_status",
            "description": "Check the current mocked platform and component status snapshot.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_http_status",
            "description": "Look up meaning, retryability, and the next diagnostic check for an HTTP code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "integer",
                        "minimum": 100,
                        "maximum": 599,
                        "description": "Observed HTTP response status code.",
                    }
                },
                "required": ["code"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_internal_docs",
            "description": "Search the mocked support documentation for a focused troubleshooting topic.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Focused query using confirmed symptoms, codes, or failure type.",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_engineering_escalation",
            "description": (
                "Create an evidence-based engineering handoff from the full accumulated case state. "
                "Use when troubleshooting cannot safely resolve the case."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "case_state": {
                        "type": "object",
                        "description": "Current serialized CaseState; unknown fields should remain empty.",
                        "properties": {
                            "case_id": {"type": ["string", "null"]},
                            "original_message": {"type": ["string", "null"]},
                            "customer": {},
                            "issue_category": {},
                            "severity": {},
                            "customer_impact": {},
                            "timestamps": {"type": "array", "items": {}},
                            "request_ids": {"type": "array", "items": {"type": "string"}},
                            "http_codes": {"type": "array", "items": {"type": "integer"}},
                            "webhook_ids": {"type": "array", "items": {"type": "string"}},
                            "evidence": {"type": "array", "items": {}},
                            "actions_taken": {"type": "array", "items": {}},
                            "missing_information": {"type": "array", "items": {}},
                            "hypotheses": {"type": "array", "items": {}},
                            "next_action": {},
                            "escalation_required": {"type": "boolean"},
                            "resolution": {},
                        },
                        "additionalProperties": True,
                    }
                },
                "required": ["case_state"],
                "additionalProperties": False,
            },
        },
    },
]


TOOL_REGISTRY: dict[str, Callable[..., ToolResult]] = {
    "inspect_api_request": inspect_api_request,
    "check_authentication": check_authentication,
    "inspect_webhook_delivery": inspect_webhook_delivery,
    "check_service_status": check_service_status,
    "lookup_http_status": lookup_http_status,
    "search_internal_docs": search_internal_docs,
    "create_engineering_escalation": create_engineering_escalation,
}

TOOL_SCHEMA_BY_NAME: dict[str, dict[str, Any]] = {
    schema["function"]["name"]: schema for schema in TOOL_SCHEMAS
}


def dispatch_tool(
    tool_name: str,
    arguments: Mapping[str, Any] | None = None,
    **keyword_arguments: Any,
) -> ToolResult:
    """Validate the dispatch envelope and call a registered tool.

    Tool-level validation failures are returned as structured ``ToolResult``
    objects so the agent can observe them and decide whether to ask a question,
    choose another action, or escalate.
    """

    tool = TOOL_REGISTRY.get(tool_name)
    if tool is None:
        return ToolResult(
            tool_name=tool_name or "unknown_tool",
            success=False,
            summary=f"Tool {tool_name!r} is not registered.",
            data={"found": False, "available_tools": sorted(TOOL_REGISTRY)},
            evidence=[],
            error="unknown_tool",
        )

    if arguments is not None and not isinstance(arguments, Mapping):
        return ToolResult(
            tool_name=tool_name,
            success=False,
            summary="Tool arguments must be an object/mapping.",
            data={"found": False},
            evidence=[],
            error="invalid_tool_arguments",
        )

    call_arguments = dict(arguments or {})
    call_arguments.update(keyword_arguments)
    try:
        return tool(**call_arguments)
    except TypeError as exc:
        return ToolResult(
            tool_name=tool_name,
            success=False,
            summary="Tool arguments did not match the registered schema.",
            data={"found": False, "provided_arguments": sorted(call_arguments)},
            evidence=[],
            error=f"invalid_tool_arguments: {exc}",
        )


def execute_tool(tool_name: str, arguments: dict[str, Any]) -> ToolResult:
    """Execute one LLM-selected tool using its JSON-decoded argument object."""

    return dispatch_tool(tool_name, arguments)


__all__ = [
    "TOOL_REGISTRY",
    "TOOL_SCHEMAS",
    "TOOL_SCHEMA_BY_NAME",
    "check_authentication",
    "check_service_status",
    "create_engineering_escalation",
    "dispatch_tool",
    "execute_tool",
    "inspect_api_request",
    "inspect_webhook_delivery",
    "lookup_http_status",
    "search_internal_docs",
]
