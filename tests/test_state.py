from __future__ import annotations

from models import Evidence, EventType, ToolResult
from state import CaseState


def test_customer_values_are_extracted_without_invention() -> None:
    message = (
        "At 2026-09-03T10:15:00Z request req_400_invalid returned 400; "
        "event evt_endpoint_500 used key_valid and endpoint https://example.test/hooks."
    )
    state = CaseState.create(message, case_id="case_extract")

    assert state.request_ids == ["req_400_invalid"]
    assert state.webhook_ids == ["evt_endpoint_500"]
    assert state.api_key_ids == ["key_valid"]
    assert state.http_codes == [400]
    assert state.timestamps == ["2026-09-03T10:15:00Z"]
    assert state.endpoints == ["https://example.test/hooks"]


def test_tool_observation_updates_state_and_emits_explicit_state_update() -> None:
    state = CaseState.create("The API failed.", case_id="case_observe")
    result = ToolResult(
        tool_name="synthetic_test_tool",
        success=True,
        summary="Found a request.",
        data={
            "found": True,
            "request": {
                "request_id": "req_confirmed_by_tool",
                "status_code": 500,
                "occurred_at": "2026-09-03T10:00:00Z",
            },
        },
        evidence=[
            Evidence(
                source="test_log",
                fact="The confirmed request returned 500.",
                identifiers={"request_id": "req_confirmed_by_tool", "http_status": "500"},
            )
        ],
    )

    state.record_tool_result(result)

    assert state.request_ids == ["req_confirmed_by_tool"]
    assert state.http_codes == [500]
    assert state.timestamps == ["2026-09-03T10:00:00Z"]
    assert state.evidence == result.evidence
    assert [event.event_type for event in state.audit_trail[-2:]] == [
        EventType.TOOL_RESULT,
        EventType.STATE_UPDATE,
    ]


def test_failed_tool_result_cannot_seed_new_identifier_provenance() -> None:
    state = CaseState.create("Something failed.", case_id="case_failed_tool")
    state.record_tool_result(
        ToolResult(
            tool_name="inspect_api_request",
            success=False,
            summary="No record found.",
            data={"found": False, "request_id": "req_not_from_customer"},
            error="unknown_request_id",
        )
    )

    assert state.request_ids == []
    assert state.evidence == []


def test_resume_preserves_case_identity_and_prior_audit() -> None:
    state = CaseState.create("Our webhook isn't firing.", case_id="case_resume")
    state.missing_information = ["event_id", "endpoint", "timestamp"]
    before_events = len(state.audit_trail)

    state.add_customer_message(
        "Event evt_endpoint_500 should reach https://example.test/hooks at 2026-09-03T09:05:00Z."
    )

    assert state.case_id == "case_resume"
    assert len(state.audit_trail) == before_events + 1
    assert state.missing_information == []
    assert state.webhook_ids == ["evt_endpoint_500"]
