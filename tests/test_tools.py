from __future__ import annotations

import json

import pytest

from models import EngineeringEscalation, ToolResult
from state import CaseState
from tools import TOOL_REGISTRY, TOOL_SCHEMA_BY_NAME, execute_tool


@pytest.mark.parametrize(
    ("request_id", "expected_code"),
    [
        ("req_400_invalid", 400),
        ("req_401_expired", 401),
        ("req_403_scope", 403),
        ("req_409_duplicate", 409),
        ("req_412_stale", 412),
        ("req_429_burst", 429),
        ("req_500_internal", 500),
        ("req_502_upstream", 502),
        ("req_503_unavailable", 503),
    ],
)
def test_api_tool_returns_structured_results(request_id: str, expected_code: int) -> None:
    result = execute_tool("inspect_api_request", {"request_id": request_id})

    assert isinstance(result, ToolResult)
    assert result.success is True
    assert result.data["found"] is True
    assert result.data["status_code"] == expected_code
    assert result.data["request"]["request_id"] == request_id
    assert result.evidence
    json.dumps(result.model_dump(mode="json"))


@pytest.mark.parametrize(
    ("event_id", "outcome", "response_code"),
    [
        ("evt_delivered", "delivered", 204),
        ("evt_endpoint_500", "endpoint_5xx", 503),
        ("evt_endpoint_timeout", "endpoint_timeout", None),
        ("evt_signature_401", "endpoint_401", 401),
        ("evt_subscription_disabled", "subscription_disabled", None),
        ("evt_no_subscription", "no_subscription", None),
    ],
)
def test_webhook_tool_distinguishes_delivery_layers(
    event_id: str, outcome: str, response_code: int | None
) -> None:
    result = execute_tool("inspect_webhook_delivery", {"event_id": event_id})

    assert result.success is True
    assert result.data["outcome"] == outcome
    assert result.data["response_code"] == response_code
    assert result.data["webhook_delivery"]["event_id"] == event_id


def test_endpoint_401_does_not_claim_signature_cause() -> None:
    result = execute_tool("inspect_webhook_delivery", {"event_id": "evt_signature_401"})

    assert result.data["outcome"] == "endpoint_401"
    assert "signature_rejected" not in json.dumps(result.model_dump(mode="json"))
    assert "hypothesis" in result.data["webhook_delivery"]["diagnostic_note"].lower()


@pytest.mark.parametrize(
    ("tool_name", "arguments", "identifier_field"),
    [
        ("inspect_api_request", {"request_id": "req_unknown"}, "request_id"),
        ("check_authentication", {"api_key_id": "key_unknown"}, "api_key_id"),
        ("inspect_webhook_delivery", {"event_id": "evt_unknown"}, "event_id"),
    ],
)
def test_unknown_identifiers_return_explicit_miss_without_logs(
    tool_name: str, arguments: dict[str, str], identifier_field: str
) -> None:
    result = execute_tool(tool_name, arguments)

    assert result.success is False
    assert result.data["found"] is False
    assert result.data[identifier_field] == arguments[identifier_field]
    assert result.evidence == []
    assert result.error


def test_every_registered_tool_has_schema_and_structured_dispatch_errors() -> None:
    assert set(TOOL_REGISTRY) == set(TOOL_SCHEMA_BY_NAME)

    unknown = execute_tool("not_a_tool", {})
    malformed = execute_tool("inspect_api_request", {})
    assert isinstance(unknown, ToolResult) and unknown.success is False
    assert isinstance(malformed, ToolResult) and malformed.success is False


@pytest.mark.parametrize("code", [400, 401, 403, 409, 412, 429, 500, 502, 503, 504])
def test_http_lookup_is_structured(code: int) -> None:
    result = execute_tool("lookup_http_status", {"code": code})

    assert result.success is True
    assert result.data["status"]["code"] == code
    assert result.data["status"]["meaning"]


def test_escalation_tool_contains_exact_required_contract() -> None:
    state = CaseState.create(
        "Request req_500_internal returned 500 at 2026-09-03T08:52:19Z.",
        customer="Synthetic Customer",
        case_id="case_escalation_test",
    )
    state.customer_impact = "Payment attempts are blocked"
    payload = state.model_dump(mode="json", exclude={"escalation"})
    payload["escalation_reason"] = "Confirmed server-side failure"

    result = execute_tool("create_engineering_escalation", {"case_state": payload})
    escalation = EngineeringEscalation.model_validate(result.data["escalation"])

    assert result.success is True
    assert set(result.data["escalation"]) == set(EngineeringEscalation.model_fields)
    assert escalation.case_id == "case_escalation_test"
    assert escalation.customer_impact == "Payment attempts are blocked"
    assert escalation.request_ids == ["req_500_internal"]
    assert escalation.http_status_codes == [500]
    assert escalation.likely_root_cause == "Undetermined from available evidence."
