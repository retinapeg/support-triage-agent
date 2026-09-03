from __future__ import annotations

from copy import deepcopy

from agent import SupportTriageAgent
from demo import SCENARIOS, run_scenario
from llm import MockDecisionAdapter
from models import (
    AgentDecision,
    CaseStatus,
    DecisionAction,
    EngineeringEscalation,
    EventType,
    HttpStatusContext,
    IssueCategory,
)


class SequenceAdapter:
    name = "sequence"

    def __init__(self, decisions: list[AgentDecision]) -> None:
        self.decisions = decisions
        self.calls = 0
        self.seen_states = []

    def decide(self, state, tool_schemas):
        self.seen_states.append(deepcopy(state))
        decision = self.decisions[min(self.calls, len(self.decisions) - 1)]
        self.calls += 1
        return decision


class InfiniteStatusAdapter:
    name = "infinite-status"

    def __init__(self) -> None:
        self.calls = 0

    def decide(self, state, tool_schemas):
        self.calls += 1
        return AgentDecision(
            action=DecisionAction.CALL_TOOL,
            rationale="Repeat status check forever unless the outer loop stops me.",
            tool_name="check_service_status",
        )


def test_outer_loop_observes_tool_result_before_next_decision() -> None:
    adapter = SequenceAdapter(
        [
            AgentDecision(
                action=DecisionAction.CALL_TOOL,
                rationale="Inspect the supplied request.",
                issue_category=IssueCategory.API_REQUEST,
                tool_name="inspect_api_request",
                tool_arguments={"request_id": "req_400_invalid"},
            ),
            AgentDecision(
                action=DecisionAction.RESOLVE,
                rationale="Use the observed status evidence.",
                issue_category=IssueCategory.API_REQUEST,
                resolution="Correct the confirmed payload validation error before retrying.",
            ),
        ]
    )
    state = SupportTriageAgent(adapter, max_steps=4).run(
        "Request req_400_invalid returned HTTP 400."
    )

    assert adapter.calls == 2
    assert adapter.seen_states[0].tool_results == []
    assert adapter.seen_states[1].tool_results[0].tool_name == "inspect_api_request"
    assert state.status == CaseStatus.RESOLVED
    assert EventType.STATE_UPDATE in [event.event_type for event in state.audit_trail]


def test_unverified_identifier_is_rejected_before_dispatch() -> None:
    adapter = SequenceAdapter(
        [
            AgentDecision(
                action=DecisionAction.CALL_TOOL,
                rationale="Try a made-up ID.",
                tool_name="inspect_api_request",
                tool_arguments={"request_id": "req_invented"},
            )
        ]
    )
    state = SupportTriageAgent(adapter, max_steps=4).run("The integration failed.")

    assert state.status == CaseStatus.AWAITING_CUSTOMER
    assert state.request_ids == []
    assert state.tool_results == []
    assert not any(event.event_type == EventType.TOOL_CALL for event in state.audit_trail)
    assert any(event.event_type == EventType.GUARDRAIL for event in state.audit_trail)


def test_unsupported_model_resolution_is_rejected() -> None:
    adapter = SequenceAdapter(
        [
            AgentDecision(
                action=DecisionAction.RESOLVE,
                rationale="Claim a fix without checking anything.",
                resolution="It is fixed.",
            )
        ]
    )
    state = SupportTriageAgent(adapter, max_steps=4).run("Something is broken.")

    assert state.status == CaseStatus.AWAITING_CUSTOMER
    assert state.resolution is None
    assert state.stop_reason == "Guardrail rejected an unsupported resolution"


def test_unrelated_evidence_cannot_support_fabricated_resolution() -> None:
    adapter = SequenceAdapter(
        [
            AgentDecision(
                action=DecisionAction.CALL_TOOL,
                rationale="Check aggregate status.",
                issue_category=IssueCategory.API_REQUEST,
                tool_name="check_service_status",
            ),
            AgentDecision(
                action=DecisionAction.RESOLVE,
                rationale="Invent a request result from unrelated evidence.",
                issue_category=IssueCategory.API_REQUEST,
                resolution="Confirmed request req_fabricated returned HTTP 200 and the issue has been fixed.",
            ),
        ]
    )
    state = SupportTriageAgent(adapter, max_steps=4).run("A request failed.")

    assert state.status == CaseStatus.AWAITING_CUSTOMER
    assert state.resolution is None
    assert state.request_ids == []
    assert any(event.event_type == EventType.GUARDRAIL for event in state.audit_trail)


def test_model_cannot_invent_customer_impact_for_escalation() -> None:
    adapter = SequenceAdapter(
        [
            AgentDecision(
                action=DecisionAction.ESCALATE,
                rationale="Escalate an uncertain case.",
                escalation_reason="Insufficient information for diagnosis.",
                customer_impact="All customer payments are blocked",
            )
        ]
    )
    state = SupportTriageAgent(adapter, max_steps=3).run("A request failed.")

    assert state.status == CaseStatus.ESCALATED
    assert state.customer_impact is None
    assert state.escalation is not None
    assert state.escalation.customer_impact == "Not established from available case evidence."
    assert any(
        event.event_type == EventType.GUARDRAIL and "Customer-impact" in (event.message or "")
        for event in state.audit_trail
    )


def test_maximum_steps_has_no_off_by_one_and_escalates() -> None:
    adapter = InfiniteStatusAdapter()
    state = SupportTriageAgent(adapter, max_steps=3).run("Intermittent system problem.")

    diagnostics = [result for result in state.tool_results if result.tool_name == "check_service_status"]
    decisions = [event for event in state.audit_trail if event.event_type == EventType.DECISION]
    assert adapter.calls == 3
    assert state.step_count == 3
    assert len(diagnostics) == 3
    assert len(decisions) == 3
    assert state.status == CaseStatus.ESCALATED
    assert state.stop_reason == "maximum_steps_reached"
    assert isinstance(state.escalation, EngineeringEscalation)


def test_awaiting_customer_does_not_spin_without_new_input() -> None:
    adapter = MockDecisionAdapter()
    agent = SupportTriageAgent(adapter, max_steps=8)
    state = agent.run("Our webhook isn't firing.")
    step_count = state.step_count
    audit_count = len(state.audit_trail)

    returned = agent.run_case(state)

    assert returned is state
    assert state.step_count == step_count
    assert len(state.audit_trail) == audit_count


def test_401_scenario_asks_then_resumes_same_case_and_resolves_sample() -> None:
    agent = SupportTriageAgent(MockDecisionAdapter(), max_steps=8)
    state = agent.run(SCENARIOS[0].issue, customer=SCENARIOS[0].customer)
    case_id = state.case_id
    first_audit_count = len(state.audit_trail)

    assert state.status == CaseStatus.AWAITING_CUSTOMER
    assert any("request ID" in question for question in state.clarification_questions)
    assert any("non-secret" in question for question in state.clarification_questions)

    state = agent.resume(state, SCENARIOS[0].followups[0])

    assert state.case_id == case_id
    assert len(state.audit_trail) > first_audit_count
    assert state.status == CaseStatus.RESOLVED
    assert state.issue_category == IssueCategory.AUTHENTICATION
    assert "inspected sample" in (state.resolution or "")
    assert [result.tool_name for result in state.tool_results] == [
        "check_authentication",
        "inspect_api_request",
    ]


def test_webhook_scenario_inspects_exact_event_and_endpoint_response() -> None:
    agent = SupportTriageAgent(MockDecisionAdapter(), max_steps=8)
    state = agent.run(SCENARIOS[1].issue, customer=SCENARIOS[1].customer)
    state = agent.resume(state, SCENARIOS[1].followups[0])

    calls = [event for event in state.audit_trail if event.event_type == EventType.TOOL_CALL]
    assert state.status == CaseStatus.RESOLVED
    assert state.issue_category == IssueCategory.WEBHOOK
    assert calls[0].tool_name == "inspect_webhook_delivery"
    assert calls[0].arguments == {"event_id": "evt_endpoint_500"}
    assert "customer endpoint returned a 5xx" in (state.resolution or "")


def test_delivered_webhook_with_204_reaches_resolution() -> None:
    state = SupportTriageAgent(MockDecisionAdapter(), max_steps=6).run(
        "Webhook event evt_delivered is missing from our app at 2026-09-03T08:12:00Z."
    )

    assert state.status == CaseStatus.RESOLVED
    assert 204 in state.http_codes
    assert "Delivery is confirmed" in (state.resolution or "")


def test_retired_webhook_endpoint_with_410_reaches_resolution() -> None:
    state = SupportTriageAgent(MockDecisionAdapter(), max_steps=6).run(
        "Webhook event evt_endpoint_gone did not arrive at 2026-09-03T08:58:00Z."
    )

    assert state.status == CaseStatus.RESOLVED
    assert 410 in state.http_codes
    assert "410 Gone" in (state.resolution or "")


def test_webhook_receiver_401_is_not_misclassified_as_api_authentication() -> None:
    state = SupportTriageAgent(MockDecisionAdapter(), max_steps=6).run(
        "Webhook event evt_signature_401 received HTTP 401 from our endpoint at "
        "2026-09-03T08:39:00Z."
    )

    tool_names = [result.tool_name for result in state.tool_results]
    assert state.status == CaseStatus.RESOLVED
    assert state.issue_category == IssueCategory.WEBHOOK
    assert tool_names == ["inspect_webhook_delivery", "lookup_http_status"]
    assert "hypothesis" in (state.resolution or "")
    assert "check_authentication" not in tool_names


def test_complex_scenario_produces_complete_engineering_escalation() -> None:
    state = run_scenario(SCENARIOS[2], provider="mock")

    assert state.status == CaseStatus.ESCALATED
    assert state.escalation_required is True
    assert state.escalation is not None
    assert set(state.escalation.model_dump()) == set(EngineeringEscalation.model_fields)
    assert state.escalation.request_ids == ["req_500_internal"]
    assert state.escalation.webhook_ids == ["evt_endpoint_timeout"]
    assert state.escalation.http_status_codes == [500]
    assert "not yet confirmed" in state.escalation.escalation_reason
    assert not any(
        "create engineering escalation" in action.lower()
        for action in state.escalation.troubleshooting_performed
    )


def test_original_combined_401_and_webhook_example_keeps_status_provenance() -> None:
    agent = SupportTriageAgent(MockDecisionAdapter(), max_steps=8)
    state = agent.run(
        "Our API integration stopped working this morning. We're getting intermittent 401s "
        "and some webhooks aren't arriving."
    )

    assert state.status == CaseStatus.AWAITING_CUSTOMER
    assert any("request ID" in question for question in state.clarification_questions)
    assert any("webhook event ID" in question for question in state.clarification_questions)

    state = agent.resume(
        state,
        "Request req_401_expired used non-secret key ID key_expired at "
        "2026-09-03T08:20:00Z. Webhook event evt_endpoint_500 should reach "
        "https://hooks.customer.example/evaluations. Production checkout requests are failing for some users.",
    )

    tool_names = [result.tool_name for result in state.tool_results]
    api_codes = {
        item.code
        for item in state.http_status_observations
        if item.context == HttpStatusContext.API_REQUEST
    }
    receiver_codes = {
        item.code
        for item in state.http_status_observations
        if item.context == HttpStatusContext.WEBHOOK_ENDPOINT
    }
    assert state.status == CaseStatus.RESOLVED
    assert tool_names == [
        "inspect_api_request",
        "check_authentication",
        "inspect_webhook_delivery",
    ]
    assert api_codes == {401}
    assert receiver_codes == {503}
    assert "Separately" in (state.resolution or "")
    assert "do not prove a shared cause" in (state.resolution or "")
    assert "API 5xx" not in (state.resolution or "")


def test_generic_clarification_fields_clear_when_customer_supplies_them() -> None:
    agent = SupportTriageAgent(MockDecisionAdapter(), max_steps=8)
    state = agent.run("Something is broken.")
    assert "request_id_or_event_id" in state.missing_information
    assert "error" in state.missing_information

    state = agent.resume(
        state,
        "Request req_500_internal returned HTTP 500 at 2026-09-03T08:52:19Z. "
        "Customers cannot complete payments.",
    )

    assert state.status == CaseStatus.ESCALATED
    assert state.escalation is not None
    assert "request_id_or_event_id" not in state.escalation.outstanding_questions
    assert "error" not in state.escalation.outstanding_questions


def test_unknown_customer_supplied_id_is_not_given_a_substitute_record() -> None:
    agent = SupportTriageAgent(MockDecisionAdapter(), max_steps=6)
    state = agent.run(
        "Request req_does_not_exist returned 500 at 2026-09-03T10:00:00Z."
    )

    assert state.status == CaseStatus.AWAITING_CUSTOMER
    assert state.request_ids == ["req_does_not_exist"]
    inspected = state.latest_tool_result("inspect_api_request")
    assert inspected is not None and inspected.success is False
    assert inspected.data["found"] is False
    assert inspected.evidence == []
