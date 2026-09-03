"""The explicit Support Triage Agent loop.

Read this file first in an interview.  The model selects one action, Python
validates and executes it, the observation is written to CaseState, and the
updated state is presented to the model on the next iteration.
"""

from __future__ import annotations

from typing import Any, Callable
import re

from llm import DecisionAdapter, build_adapter
from models import (
    ActionRecord,
    AuditEvent,
    CaseStatus,
    DecisionAction,
    EngineeringEscalation,
    EventType,
    Severity,
    TERMINAL_STATUSES,
    ToolResult,
)
from state import API_KEY_ID_RE, REQUEST_ID_RE, WEBHOOK_ID_RE, CaseState, _append_unique
from tools import TOOL_SCHEMA_BY_NAME, TOOL_SCHEMAS, execute_tool


EventCallback = Callable[[AuditEvent, CaseState], None]


class SupportTriageAgent:
    """Run a bounded, observable decide/act/observe/update loop."""

    def __init__(
        self,
        adapter: DecisionAdapter | None = None,
        *,
        max_steps: int = 8,
        event_callback: EventCallback | None = None,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be at least 1")
        self.adapter = adapter or build_adapter()
        self.max_steps = max_steps
        self.event_callback = event_callback

    def create_case(self, issue: str, *, customer: str | None = None) -> CaseState:
        state = CaseState.create(issue, customer=customer, max_steps=self.max_steps)
        self._emit_latest(state)
        return state

    def run(self, issue: str, *, customer: str | None = None) -> CaseState:
        """Create and investigate a new case until the first stop condition."""

        return self.run_case(self.create_case(issue, customer=customer))

    def resume(self, state: CaseState, customer_response: str) -> CaseState:
        """Resume the same case after a customer supplies clarification."""

        if state.status in TERMINAL_STATUSES:
            raise ValueError(f"Cannot resume a terminal case ({state.status.value})")
        state.add_customer_message(customer_response)
        self._emit_latest(state)
        return self.run_case(state)

    def run_case(self, state: CaseState) -> CaseState:
        """Continue a case while budget remains and no stop condition is met."""

        if state.status in TERMINAL_STATUSES:
            return state
        if state.status == CaseStatus.AWAITING_CUSTOMER:
            # Running again without new evidence must not spin or spend budget.
            return state

        decision_tools = [
            schema
            for schema in TOOL_SCHEMAS
            if schema.get("function", {}).get("name") != "create_engineering_escalation"
        ]
        while state.step_count < state.max_steps:
            try:
                decision = self.adapter.decide(state.model_copy(deep=True), decision_tools)
            except Exception as exc:  # provider failures are bounded and audited
                self._record_adapter_failure(state, exc)
                break

            state.begin_step(decision)
            self._emit_latest(state)

            if decision.action == DecisionAction.ASK_CUSTOMER:
                state.clarification_questions = decision.questions
                state.next_action = "Wait for customer clarification"
                state.stop(reason="Required customer information is missing", status=CaseStatus.AWAITING_CUSTOMER)
                self._emit_latest(state)
                return state

            if decision.action == DecisionAction.CALL_TOOL:
                arguments = dict(decision.tool_arguments)
                guard_error = self._validate_tool_call(decision.tool_name or "", arguments, state)
                if guard_error:
                    state.record_guardrail(guard_error, tool_name=decision.tool_name)
                    self._emit_latest(state)
                    self._stop_for_rejected_identifier(state, decision.tool_name or "")
                    return state

                state.record_tool_call(decision.tool_name or "", arguments)
                self._emit_latest(state)
                try:
                    result = execute_tool(decision.tool_name or "", arguments)
                except Exception as exc:
                    result = ToolResult(
                        tool_name=decision.tool_name or "unknown",
                        success=False,
                        summary="Tool execution failed safely.",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                state.record_tool_result(result)
                self._emit_latest(state)
                continue

            if decision.action == DecisionAction.RESOLVE:
                resolution_error = self._validate_resolution(state, decision.resolution or "")
                if resolution_error:
                    state.record_guardrail(
                        f"Resolution rejected: {resolution_error}"
                    )
                    self._emit_latest(state)
                    _append_unique(state.missing_information, ["confirmed_diagnostic_evidence"])
                    state.clarification_questions = [
                        "Can you provide a request ID or event ID and the incident timestamp so the diagnosis can be verified?"
                    ]
                    state.next_action = "Wait for evidence required to verify the proposed resolution"
                    state.stop(
                        reason="Guardrail rejected an unsupported resolution",
                        status=CaseStatus.AWAITING_CUSTOMER,
                    )
                    self._emit_latest(state)
                    return state
                state.resolution = decision.resolution
                state.stop(reason="Evidence-supported resolution produced", status=CaseStatus.RESOLVED)
                self._emit_latest(state)
                return state

            if decision.action == DecisionAction.ESCALATE:
                self._create_escalation(state, decision.escalation_reason or "Insufficient confidence")
                return state

        # No N+1 model decision or diagnostic tool call occurs. Escalation is a
        # stop-policy action and is generated from the pre-escalation snapshot.
        if state.status not in TERMINAL_STATUSES:
            self._create_escalation(
                state,
                f"Maximum agent step limit reached ({state.max_steps}) without a safe resolution.",
                stop_reason="maximum_steps_reached",
            )
        return state

    def _record_adapter_failure(self, state: CaseState, exc: Exception) -> None:
        state.step_count += 1
        state.actions_taken.append(
            ActionRecord(
                step=state.step_count,
                action="adapter_error",
                summary="Decision adapter failed; no diagnostic tool was executed.",
            )
        )
        state.audit_trail.append(
            AuditEvent(
                step=state.step_count,
                event_type=EventType.GUARDRAIL,
                message=f"Decision adapter error: {type(exc).__name__}: {exc}",
            )
        )
        self._emit_latest(state)

    @staticmethod
    def _validate_resolution(state: CaseState, resolution: str) -> str | None:
        """Require relevant observations and reject invented success claims."""

        relevant_tools = {
            "authentication": {"check_authentication", "inspect_api_request"},
            "webhook": {"inspect_webhook_delivery"},
            "api_request": {"inspect_api_request"},
            "rate_limiting": {"inspect_api_request"},
            "conflict_state": {"inspect_api_request"},
            "configuration": {"search_internal_docs", "inspect_webhook_delivery"},
            "data": {"inspect_api_request", "search_internal_docs"},
            "unknown": set(),
        }
        successful = {
            result.tool_name
            for result in state.tool_results
            if result.success and result.evidence
        }
        required = relevant_tools[state.issue_category.value]
        if not required or not successful.intersection(required):
            return f"no successful {state.issue_category.value} diagnostic observation supports it"

        identifiers = {
            "request ID": (REQUEST_ID_RE.findall(resolution), state.request_ids),
            "event ID": (WEBHOOK_ID_RE.findall(resolution), state.webhook_ids),
            "API-key ID": (API_KEY_ID_RE.findall(resolution), state.api_key_ids),
        }
        for label, (mentioned, allowed) in identifiers.items():
            invented = [item for item in mentioned if item.lower() not in {str(value).lower() for value in allowed}]
            if invented:
                return f"unverified {label} {invented[0]!r} appears in the proposed resolution"

        for match in re.findall(r"\bHTTP\s+([1-5]\d{2})\b", resolution, flags=re.IGNORECASE):
            if int(match) not in state.http_codes:
                return f"unverified HTTP status {match} appears in the proposed resolution"

        customer_text = " ".join(state.customer_messages).lower()
        if re.search(r"\b(?:is|was|has been)\s+(?:fixed|resolved|restored|recovered)\b", resolution, re.IGNORECASE):
            if not re.search(r"\b(?:fixed|resolved|restored|recovered)\b", customer_text):
                return "it claims recovery even though neither the customer nor a remediation check confirmed it"
        return None

    @staticmethod
    def _validate_tool_call(tool_name: str, arguments: dict[str, Any], state: CaseState) -> str | None:
        known_tools = set(TOOL_SCHEMA_BY_NAME)
        if tool_name not in known_tools or tool_name == "create_engineering_escalation":
            return f"Tool {tool_name!r} is not available for direct diagnostic selection."

        provenance_rules: dict[str, tuple[str, list[Any]]] = {
            "inspect_api_request": ("request_id", state.request_ids),
            "check_authentication": ("api_key_id", state.api_key_ids),
            "inspect_webhook_delivery": ("event_id", state.webhook_ids),
            "lookup_http_status": ("code", state.http_codes),
        }
        if tool_name in provenance_rules:
            field, allowed = provenance_rules[tool_name]
            supplied = arguments.get(field)
            if supplied is None:
                return f"{tool_name} requires {field}; ask the customer rather than inventing it."
            if field == "code":
                try:
                    supplied = int(supplied)
                except (TypeError, ValueError):
                    return f"{field} must be an observed HTTP status code."
                arguments[field] = supplied
                if supplied not in allowed:
                    return f"HTTP status {supplied} is not present in confirmed case evidence."
            else:
                canonical = next(
                    (item for item in allowed if str(item).lower() == str(supplied).lower()),
                    None,
                )
                if canonical is None:
                    return f"{field}={supplied!r} is not present in customer input or confirmed tool evidence."
                arguments[field] = canonical
        return None

    def _stop_for_rejected_identifier(self, state: CaseState, tool_name: str) -> None:
        fields = {
            "inspect_api_request": ("request_id", "Can you provide a valid failed request ID?"),
            "check_authentication": ("api_key_id", "Can you provide the non-secret API key ID or credential label?"),
            "inspect_webhook_delivery": ("event_id", "Can you provide the event ID to trace?"),
            "lookup_http_status": ("http_status", "What HTTP status was actually observed?"),
        }
        field, question = fields.get(
            tool_name,
            ("valid_tool_input", "Can you provide the missing diagnostic detail?"),
        )
        _append_unique(state.missing_information, [field])
        state.clarification_questions = [question]
        state.next_action = "Wait for customer clarification"
        state.stop(reason="Guardrail rejected an unverified tool argument", status=CaseStatus.AWAITING_CUSTOMER)
        self._emit_latest(state)

    def _create_escalation(
        self,
        state: CaseState,
        reason: str,
        *,
        stop_reason: str | None = None,
    ) -> None:
        # Exclude the escalation decision/tool itself from "troubleshooting
        # performed" to prevent a recursive hand-off narrative.
        snapshot = state.model_dump(mode="json", exclude={"escalation"})
        snapshot["actions_taken"] = [
            action for action in snapshot["actions_taken"] if action.get("action") != "escalate"
        ]
        snapshot["escalation_reason"] = reason
        state.escalation_required = True
        arguments = {"case_state": snapshot}
        state.record_tool_call("create_engineering_escalation", {"case_id": state.case_id})
        self._emit_latest(state)
        try:
            result = execute_tool("create_engineering_escalation", arguments)
            state.record_tool_result(result)
            if not result.success:
                raise RuntimeError(result.error or result.summary)
            payload = result.data.get("escalation", result.data)
            state.escalation = EngineeringEscalation.model_validate(payload)
        except Exception as exc:
            state.record_guardrail(
                f"Escalation tool failed validation: {type(exc).__name__}: {exc}",
                tool_name="create_engineering_escalation",
            )
            state.escalation = self._fallback_escalation(state, reason)
        state.stop(reason=stop_reason or reason, status=CaseStatus.ESCALATED)
        self._emit_latest(state)

    @staticmethod
    def _fallback_escalation(state: CaseState, reason: str) -> EngineeringEscalation:
        """Fail closed with explicit unknowns if the formatting tool fails."""

        return EngineeringEscalation(
            case_id=state.case_id,
            customer=state.customer or "Not provided",
            severity=state.severity,
            escalation_reason=reason,
            customer_impact=state.customer_impact or "Not provided by customer",
            symptoms=[state.original_message],
            timestamps=list(state.timestamps),
            request_ids=list(state.request_ids),
            http_status_codes=list(state.http_codes),
            webhook_ids=list(state.webhook_ids),
            relevant_details={
                "confirmed_evidence": [item.model_dump(mode="json") for item in state.evidence]
            },
            troubleshooting_performed=[
                item.summary for item in state.actions_taken if item.action not in {"escalate", "adapter_error"}
            ],
            likely_root_cause=(
                state.hypotheses[-1].statement
                if state.hypotheses
                else "Unknown - not established from current evidence"
            ),
            outstanding_questions=list(state.missing_information),
        )

    def _emit_latest(self, state: CaseState) -> None:
        if self.event_callback and state.audit_trail:
            self.event_callback(state.audit_trail[-1], state)
