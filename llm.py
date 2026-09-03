"""Swappable decision adapters for the support-triage loop.

MockDecisionAdapter is deterministic and credential-free. OpenAIResponsesAdapter
uses the same AgentDecision contract, leaving orchestration and tool execution in
agent.py instead of hiding the loop inside an agent framework.
"""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

from models import AgentDecision, DecisionAction, HttpStatusContext, IssueCategory, Severity
from state import CaseState


class DecisionAdapter(Protocol):
    name: str

    def decide(self, state: CaseState, tool_schemas: list[dict[str, Any]]) -> AgentDecision:
        """Select exactly one next action from the current case state."""


SYSTEM_INSTRUCTIONS = """You are the decision component inside a technical-support
triage agent. Select exactly one next action. You do not execute tools yourself;
the outer Python loop validates and executes your selected tool.

Rules:
- Use only customer details and confirmed tool evidence in CaseState as facts.
- Keep hypotheses explicitly labelled as hypotheses.
- Never invent a request ID, event ID, API-key ID, timestamp, log, payload, or customer.
- Tool identifier arguments must already appear in CaseState.
- customer_impact must be a short literal span from a customer message, not a paraphrase or inference.
- Ask concise targeted questions when a required identifier or incident detail is missing.
- Prefer the smallest relevant diagnostic tool call; do not repeat completed calls.
- Resolve only when evidence supports a safe conclusion.
- Escalate when there is likely platform failure, conflicting evidence, material impact with
  insufficient confidence, or the remaining step budget is too small for safe diagnosis.
- Do not claim the customer performed a recommended remediation unless they said so.
"""


class OpenAIResponsesAdapter:
    """Optional live adapter using the OpenAI Responses API structured output."""

    name = "openai"

    def __init__(self, *, model: str | None = None, api_key: str | None = None) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "OpenAI mode requires the optional dependency: pip install -e '.[openai]'"
            ) from exc
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
        self._client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))

    def decide(self, state: CaseState, tool_schemas: list[dict[str, Any]]) -> AgentDecision:
        payload = {
            "case_state": state.model_dump(mode="json", exclude={"audit_trail"}),
            "available_tools": tool_schemas,
            "remaining_steps": state.max_steps - state.step_count,
        }
        response = self._client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": SYSTEM_INSTRUCTIONS},
                {"role": "user", "content": json.dumps(payload)},
            ],
            text_format=AgentDecision,
            store=False,
        )
        if response.output_parsed is None:
            raise RuntimeError("The model returned no decision payload")
        return response.output_parsed


class MockDecisionAdapter:
    """Deterministic stand-in for an LLM, exercising the identical outer loop."""

    name = "mock"

    def decide(self, state: CaseState, tool_schemas: list[dict[str, Any]]) -> AgentDecision:
        category = self._classify(state)
        severity = self._severity(state)
        impact = self._impact(state)

        unknown_result = self._latest_unknown_identifier_result(state)
        if unknown_result is not None:
            identifier = next(
                (
                    value
                    for key, value in unknown_result.data.items()
                    if key in {"request_id", "event_id", "api_key_id"}
                ),
                "that identifier",
            )
            return AgentDecision(
                action=DecisionAction.ASK_CUSTOMER,
                rationale="The supplied identifier was not found, so it must be verified rather than guessed.",
                issue_category=category,
                severity=severity,
                questions=[f"Could you verify {identifier} and confirm which environment it came from?"],
                missing_information=["verified_identifier"],
                confidence=0.95,
            )

        if self._is_combined_auth_webhook(state):
            return self._combined_auth_webhook_decision(state, severity, impact)
        if self._is_complex_server_case(state):
            return self._complex_server_decision(state, category, severity, impact)
        if category == IssueCategory.AUTHENTICATION:
            return self._authentication_decision(state, severity, impact)
        if category == IssueCategory.WEBHOOK:
            return self._webhook_decision(state, severity, impact)
        if category in {
            IssueCategory.RATE_LIMITING,
            IssueCategory.CONFLICT_STATE,
            IssueCategory.API_REQUEST,
        }:
            return self._api_decision(state, category, severity, impact)
        if category in {IssueCategory.CONFIGURATION, IssueCategory.DATA}:
            return self._configuration_or_data_decision(state, category, severity, impact)

        return AgentDecision(
            action=DecisionAction.ASK_CUSTOMER,
            rationale="The report does not yet contain enough evidence to choose a diagnostic safely.",
            issue_category=IssueCategory.UNKNOWN,
            severity=severity,
            customer_impact=impact,
            questions=[
                "What operation failed, and what exact error or HTTP status did you receive?",
                "Can you share a request ID or event ID and the incident timestamp with timezone?",
                "Which environment is affected, and what is the customer impact?",
            ],
            missing_information=["error", "request_id_or_event_id", "timestamp", "customer_impact"],
            hypotheses=["The issue category is not yet established."],
            confidence=0.2,
        )

    @staticmethod
    def _all_text(state: CaseState) -> str:
        return " ".join(state.customer_messages).lower()

    def _classify(self, state: CaseState) -> IssueCategory:
        text = self._all_text(state)
        if state.webhook_ids or "webhook" in text:
            # A report spanning explicit 5xx request failures and webhooks is
            # treated as an API incident, not collapsed into a webhook issue.
            if state.request_ids and any(code >= 500 for code in self._api_status_codes(state)):
                return IssueCategory.API_REQUEST
            return IssueCategory.WEBHOOK
        if any(code in {401, 403} for code in state.http_codes) or any(
            word in text for word in ("authentication", "credential", "token", "api key", "401", "403")
        ):
            return IssueCategory.AUTHENTICATION
        if 429 in state.http_codes or "rate limit" in text:
            return IssueCategory.RATE_LIMITING
        if any(code in {409, 412} for code in state.http_codes) or any(
            word in text for word in ("conflict", "precondition", "stale version")
        ):
            return IssueCategory.CONFLICT_STATE
        if any(code in {400} or code >= 500 for code in state.http_codes) or any(
            word in text for word in ("api", "request", "endpoint", "server error")
        ):
            return IssueCategory.API_REQUEST
        if "config" in text or "environment" in text:
            return IssueCategory.CONFIGURATION
        if "data" in text or "record" in text or "field" in text:
            return IssueCategory.DATA
        return IssueCategory.UNKNOWN

    def _severity(self, state: CaseState) -> Severity:
        text = self._all_text(state)
        if any(term in text for term in ("all customers", "complete outage", "cannot transact")):
            return Severity.CRITICAL
        if any(term in text for term in ("production", "payments", "many users", "stopped working", "blocked")):
            return Severity.HIGH
        if any(term in text for term in ("intermittent", "delayed", "some users")):
            return Severity.MEDIUM
        return state.severity

    def _impact(self, state: CaseState) -> str | None:
        if state.customer_impact:
            return state.customer_impact
        text = self._all_text(state)
        impact_phrases = []
        for phrase in (
            "production checkout requests are failing for some users",
            "payment status updates are delayed",
            "customers cannot complete payments",
            "all customers are blocked",
        ):
            if phrase in text:
                impact_phrases.append(phrase.capitalize())
        return "; ".join(impact_phrases) or None

    @staticmethod
    def _latest_unknown_identifier_result(state: CaseState):
        if not state.tool_results:
            return None
        latest = state.tool_results[-1]
        if latest.data.get("found") is False and latest.tool_name in {
            "inspect_api_request",
            "check_authentication",
            "inspect_webhook_delivery",
        }:
            return latest
        return None

    def _is_complex_server_case(self, state: CaseState) -> bool:
        return bool(
            state.request_ids
            and state.webhook_ids
            and any(code >= 500 for code in self._api_status_codes(state))
        )

    @staticmethod
    def _api_status_codes(state: CaseState) -> list[int]:
        return [
            observation.code
            for observation in state.http_status_observations
            if observation.context == HttpStatusContext.API_REQUEST
        ]

    def _is_combined_auth_webhook(self, state: CaseState) -> bool:
        text = self._all_text(state)
        api_language = "api" in text or "request" in text
        auth_signal = bool(
            any(code in {401, 403} for code in self._api_status_codes(state))
            or (
                api_language
                and any(
                    term in text
                    for term in ("401", "403", "authentication", "credential", "token")
                )
            )
        )
        return "webhook" in text and auth_signal

    def _combined_auth_webhook_decision(
        self, state: CaseState, severity: Severity, impact: str | None
    ) -> AgentDecision:
        base = dict(
            issue_category=IssueCategory.AUTHENTICATION,
            issue_categories=[IssueCategory.AUTHENTICATION, IssueCategory.WEBHOOK],
            severity=severity,
            customer_impact=impact,
        )
        missing: list[str] = []
        questions: list[str] = []
        if not state.request_ids and not state.api_key_ids:
            missing.extend(["request_id", "api_key_id"])
            questions.append(
                "Can you provide one failed request ID and the non-secret API-key ID or credential label?"
            )
        if not state.webhook_ids:
            missing.append("event_id")
            questions.append("Can you provide one missing webhook event ID and its expected endpoint?")
        if not state.timestamps:
            missing.append("timestamp")
            questions.append("What incident timestamp and timezone should I use for both traces?")
        if questions:
            return AgentDecision(
                action=DecisionAction.ASK_CUSTOMER,
                rationale="The report contains separate authentication and webhook symptoms, so each needs its own correlation identifier.",
                questions=questions,
                missing_information=missing,
                hypotheses=["The authentication and webhook symptoms may be independent; no shared cause is established."],
                confidence=0.55,
                **base,
            )

        if state.request_ids and not state.tool_was_called("inspect_api_request"):
            return AgentDecision(
                action=DecisionAction.CALL_TOOL,
                rationale="Inspect the failed API request and preserve its status separately from webhook receiver responses.",
                tool_name="inspect_api_request",
                tool_arguments={"request_id": state.request_ids[0]},
                confidence=0.8,
                **base,
            )
        if state.api_key_ids and not state.tool_was_called("check_authentication"):
            return AgentDecision(
                action=DecisionAction.CALL_TOOL,
                rationale="Check the credential associated with the authentication symptom.",
                tool_name="check_authentication",
                tool_arguments={"api_key_id": state.api_key_ids[0]},
                confidence=0.8,
                **base,
            )
        if state.webhook_ids and not state.tool_was_called("inspect_webhook_delivery"):
            return AgentDecision(
                action=DecisionAction.CALL_TOOL,
                rationale="Trace the webhook independently through event generation, delivery, and receiver response.",
                tool_name="inspect_webhook_delivery",
                tool_arguments={"event_id": state.webhook_ids[0]},
                confidence=0.8,
                **base,
            )

        request = state.latest_tool_result("inspect_api_request")
        auth = state.latest_tool_result("check_authentication")
        delivery = state.latest_tool_result("inspect_webhook_delivery")
        request_code = request.data.get("status_code") if request else None
        auth_state = auth.data.get("status") if auth else None
        delivery_outcome = delivery.data.get("outcome") if delivery else None

        if request_code and int(request_code) >= 500:
            return AgentDecision(
                action=DecisionAction.ESCALATE,
                rationale="The API trace confirms a server-side failure; the webhook receiver result remains separately scoped.",
                escalation_reason="Confirmed API 5xx plus a separate webhook symptom requires engineering review.",
                confidence=0.88,
                **base,
            )
        if auth_state == "valid" and request_code in {401, 403}:
            return AgentDecision(
                action=DecisionAction.ESCALATE,
                rationale="The request failure conflicts with the credential registry, while a second webhook symptom is also present.",
                escalation_reason="Conflicting authentication evidence and a separate webhook failure require deeper investigation.",
                confidence=0.75,
                **base,
            )
        if auth_state in {"expired", "revoked", "missing_scope"} and delivery_outcome in {
            "delivered",
            "endpoint_5xx",
            "endpoint_timeout",
            "endpoint_401",
            "endpoint_gone",
            "subscription_disabled",
            "no_subscription",
        }:
            auth_actions = {
                "expired": "refresh or rotate the expired credential",
                "revoked": "replace the revoked credential",
                "missing_scope": "grant the required permission",
            }
            webhook_findings = {
                "delivered": "delivery succeeded, so inspect consumer processing",
                "endpoint_5xx": "the receiving endpoint returned a 5xx, so repair it before a safe replay",
                "endpoint_timeout": "the receiving endpoint timed out and should acknowledge quickly before asynchronous work",
                "endpoint_401": "the receiving endpoint returned 401; verify receiver authentication/signature settings",
                "endpoint_gone": "the receiving endpoint returned 410 and must be replaced",
                "subscription_disabled": "the webhook subscription was disabled",
                "no_subscription": "no matching webhook subscription existed",
            }
            return AgentDecision(
                action=DecisionAction.RESOLVE,
                rationale="The two symptom families now have separate, source-scoped observations and standard next actions.",
                resolution=(
                    f"For the inspected API sample, the credential state is {auth_state}; {auth_actions[auth_state]}. "
                    f"Separately, {webhook_findings[delivery_outcome]}. These observations do not prove a shared cause "
                    "or explain every intermittent failure; collect additional successful and failed request IDs if the pattern continues."
                ),
                confidence=0.92,
                **base,
            )
        return AgentDecision(
            action=DecisionAction.ESCALATE,
            rationale="Both symptom families were inspected, but the combined evidence has no safe standard resolution.",
            escalation_reason="Authentication and webhook evidence remain inconclusive after separate traces.",
            confidence=0.6,
            **base,
        )

    def _complex_server_decision(
        self,
        state: CaseState,
        category: IssueCategory,
        severity: Severity,
        impact: str | None,
    ) -> AgentDecision:
        base = dict(issue_category=category, severity=severity, customer_impact=impact)
        if not state.tool_was_called("check_service_status"):
            return AgentDecision(
                action=DecisionAction.CALL_TOOL,
                rationale="Check whether the multi-symptom 5xx case aligns with a known service incident.",
                tool_name="check_service_status",
                tool_arguments={},
                hypotheses=["A shared platform dependency may explain both symptoms."],
                confidence=0.55,
                **base,
            )
        if not state.tool_was_called("inspect_api_request"):
            return AgentDecision(
                action=DecisionAction.CALL_TOOL,
                rationale="Inspect the supplied failing request for a confirmed failure stage and correlation data.",
                tool_name="inspect_api_request",
                tool_arguments={"request_id": state.request_ids[0]},
                confidence=0.75,
                **base,
            )
        if not state.tool_was_called("inspect_webhook_delivery"):
            return AgentDecision(
                action=DecisionAction.CALL_TOOL,
                rationale="Inspect the supplied event to determine whether the webhook symptom is related or downstream.",
                tool_name="inspect_webhook_delivery",
                tool_arguments={"event_id": state.webhook_ids[0]},
                confidence=0.75,
                **base,
            )
        if state.http_codes and not state.tool_was_called("lookup_http_status"):
            server_code = next((code for code in state.http_codes if code >= 500), state.http_codes[0])
            return AgentDecision(
                action=DecisionAction.CALL_TOOL,
                rationale="Add the standard handling meaning for the observed server response.",
                tool_name="lookup_http_status",
                tool_arguments={"code": server_code},
                confidence=0.8,
                **base,
            )
        return AgentDecision(
            action=DecisionAction.ESCALATE,
            rationale="The reported incident window contains an internal API failure and webhook degradation and cannot be safely resolved at triage.",
            escalation_reason="Combined API and webhook failures require engineering investigation; a shared cause is not yet confirmed.",
            hypotheses=["A shared platform-side processing or dependency fault is possible, but not confirmed."],
            confidence=0.82,
            **base,
        )

    def _authentication_decision(
        self, state: CaseState, severity: Severity, impact: str | None
    ) -> AgentDecision:
        base = dict(
            issue_category=IssueCategory.AUTHENTICATION,
            severity=severity,
            customer_impact=impact,
        )
        if not state.request_ids and not state.api_key_ids:
            missing = ["request_id", "api_key_id"]
            if not state.timestamps:
                missing.append("timestamp")
            return AgentDecision(
                action=DecisionAction.ASK_CUSTOMER,
                rationale="A 401/403 cannot be tied to authentication evidence without a request or credential identifier.",
                questions=[
                    "Can you provide one failed request ID and its timestamp with timezone?",
                    "What non-secret API key ID or credential label was used (do not send the secret)?",
                    "Which environment is affected, and did the failure begin after a token or configuration change?",
                ],
                missing_information=missing,
                hypotheses=[
                    "The credential may be expired, revoked, scoped incorrectly, or sent to the wrong environment."
                ],
                confidence=0.45,
                **base,
            )
        if state.api_key_ids and not state.tool_was_called("check_authentication"):
            return AgentDecision(
                action=DecisionAction.CALL_TOOL,
                rationale="Validate the supplied non-secret credential identifier before inferring an authentication cause.",
                tool_name="check_authentication",
                tool_arguments={"api_key_id": state.api_key_ids[0]},
                confidence=0.7,
                **base,
            )
        if state.request_ids and not state.tool_was_called("inspect_api_request"):
            return AgentDecision(
                action=DecisionAction.CALL_TOOL,
                rationale="Correlate the customer report with the actual request outcome.",
                tool_name="inspect_api_request",
                tool_arguments={"request_id": state.request_ids[0]},
                confidence=0.8,
                **base,
            )
        auth = state.latest_tool_result("check_authentication")
        request = state.latest_tool_result("inspect_api_request")
        auth_state = (auth.data.get("status") if auth else None) or (request.data.get("auth_result") if request else None)
        if auth_state in {"expired", "token_expired", "revoked", "missing_scope"}:
            actions = {
                "expired": "issue a new credential or token and retry the failed operation",
                "token_expired": "refresh the bearer token and retry with the new token",
                "revoked": "replace the revoked credential through the approved rotation process",
                "missing_scope": "grant the required permission to the credential, then retry",
            }
            return AgentDecision(
                action=DecisionAction.RESOLVE,
                rationale="Authentication and request evidence identify a concrete credential condition.",
                resolution=(
                    f"Confirmed for the inspected sample: {auth_state.replace('_', ' ')}. Recommended action: "
                    f"{actions[auth_state]}. This explains the inspected failure but does not prove every intermittent "
                    "failure has the same cause; compare additional failing and successful request IDs if the pattern "
                    "continues. The agent has not claimed the customer already made this change."
                ),
                confidence=0.95,
                **base,
            )
        if auth and auth.data.get("status") == "valid":
            return AgentDecision(
                action=DecisionAction.ESCALATE,
                rationale="The credential appears valid but the request still failed authentication, leaving conflicting evidence.",
                escalation_reason="Valid credential state conflicts with the observed authentication failure.",
                confidence=0.7,
                **base,
            )
        return AgentDecision(
            action=DecisionAction.ASK_CUSTOMER,
            rationale="The remaining authentication evidence is incomplete.",
            questions=["Can you provide a failed request ID and timestamp from the same credential attempt?"],
            missing_information=["request_id", "timestamp"],
            confidence=0.4,
            **base,
        )

    def _webhook_decision(
        self, state: CaseState, severity: Severity, impact: str | None
    ) -> AgentDecision:
        base = dict(issue_category=IssueCategory.WEBHOOK, severity=severity, customer_impact=impact)
        if not state.webhook_ids:
            missing = ["event_id"]
            if not state.endpoints:
                missing.append("endpoint")
            if not state.timestamps:
                missing.append("timestamp")
            return AgentDecision(
                action=DecisionAction.ASK_CUSTOMER,
                rationale="Webhook generation, delivery, and endpoint processing cannot be separated without an event identifier.",
                questions=[
                    "What event ID should I trace?",
                    "What receiving endpoint was expected, and in which environment?",
                    "When should it have fired (timestamp and timezone), and was any response recorded?",
                ],
                missing_information=missing,
                hypotheses=[
                    "The event may not have been generated, delivery may have failed, or the receiving endpoint may have rejected it."
                ],
                confidence=0.4,
                **base,
            )
        if not state.tool_was_called("inspect_webhook_delivery"):
            return AgentDecision(
                action=DecisionAction.CALL_TOOL,
                rationale="Trace the supplied event through generation and delivery before choosing a remediation.",
                tool_name="inspect_webhook_delivery",
                tool_arguments={"event_id": state.webhook_ids[0]},
                confidence=0.75,
                **base,
            )
        delivery = state.latest_tool_result("inspect_webhook_delivery")
        response_code = delivery.data.get("response_code") if delivery else None
        if response_code and not state.tool_was_called("lookup_http_status"):
            return AgentDecision(
                action=DecisionAction.CALL_TOOL,
                rationale="Interpret the endpoint response recorded for the delivery attempt.",
                tool_name="lookup_http_status",
                tool_arguments={"code": int(response_code)},
                confidence=0.85,
                **base,
            )
        outcome = delivery.data.get("outcome") if delivery else None
        if outcome in {"delivered", "endpoint_5xx", "endpoint_timeout", "endpoint_401", "endpoint_gone", "subscription_disabled", "no_subscription"}:
            messages = {
                "delivered": "Delivery is confirmed. Check the consumer's post-receipt processing and idempotency logs.",
                "endpoint_5xx": "The event was generated and sent, but the customer endpoint returned a 5xx. Repair the endpoint and replay the event if replay is safe.",
                "endpoint_timeout": "The endpoint did not acknowledge within the delivery timeout. Acknowledge quickly, process asynchronously, then retry or replay safely.",
                "endpoint_401": (
                    "The receiving endpoint returned 401. That confirms receiver-side rejection, not its cause. "
                    "Verify the endpoint's authentication/signature configuration and environment before retrying; "
                    "a signing-secret mismatch remains a hypothesis until receiver logs confirm it."
                ),
                "endpoint_gone": (
                    "The receiver returned 410 Gone, confirming that the configured webhook target is no longer "
                    "available. Replace the retired endpoint, verify its subscription, then replay only if safe."
                ),
                "subscription_disabled": "The subscription is disabled. Re-enable the intended subscription after confirming the endpoint and event filters.",
                "no_subscription": "No matching subscription generated a delivery. Correct the event subscription/filter configuration.",
            }
            return AgentDecision(
                action=DecisionAction.RESOLVE,
                rationale="Delivery evidence distinguishes event generation, transport, and endpoint behaviour.",
                resolution=messages[outcome],
                confidence=0.92,
                **base,
            )
        return AgentDecision(
            action=DecisionAction.ESCALATE,
            rationale="The webhook tool did not establish a safe, standard resolution.",
            escalation_reason="Webhook delivery state is inconclusive after log inspection.",
            confidence=0.55,
            **base,
        )

    def _api_decision(
        self,
        state: CaseState,
        category: IssueCategory,
        severity: Severity,
        impact: str | None,
    ) -> AgentDecision:
        base = dict(issue_category=category, severity=severity, customer_impact=impact)
        if not state.request_ids:
            return AgentDecision(
                action=DecisionAction.ASK_CUSTOMER,
                rationale="The API symptom needs a request-level correlation point.",
                questions=[
                    "Can you provide one failed request ID, timestamp with timezone, and HTTP status?",
                    "Which endpoint and environment were used, and what customer operation was affected?",
                ],
                missing_information=["request_id", "timestamp"],
                confidence=0.4,
                **base,
            )
        if any(code >= 500 for code in self._api_status_codes(state)) and not state.tool_was_called("check_service_status"):
            return AgentDecision(
                action=DecisionAction.CALL_TOOL,
                rationale="Check for a known incident before analysing an individual 5xx request.",
                tool_name="check_service_status",
                tool_arguments={},
                confidence=0.65,
                **base,
            )
        if not state.tool_was_called("inspect_api_request"):
            return AgentDecision(
                action=DecisionAction.CALL_TOOL,
                rationale="Inspect the customer-supplied request rather than infer from the status alone.",
                tool_name="inspect_api_request",
                tool_arguments={"request_id": state.request_ids[0]},
                confidence=0.8,
                **base,
            )
        if state.http_codes and not state.tool_was_called("lookup_http_status"):
            return AgentDecision(
                action=DecisionAction.CALL_TOOL,
                rationale="Add standard status semantics and recommended handling to the confirmed request evidence.",
                tool_name="lookup_http_status",
                tool_arguments={"code": state.http_codes[0]},
                confidence=0.8,
                **base,
            )
        result = state.latest_tool_result("inspect_api_request")
        code = result.data.get("status_code") if result else None
        if code and int(code) >= 500:
            return AgentDecision(
                action=DecisionAction.ESCALATE,
                rationale="The inspected request confirms a server-side failure that standard triage cannot repair.",
                escalation_reason="Confirmed server-side request failure requires engineering investigation.",
                confidence=0.88,
                **base,
            )
        resolutions = {
            400: "Correct the request payload using the returned validation details, then retry with a new request ID.",
            409: "Reconcile the duplicate or conflicting resource state before retrying idempotently.",
            412: "Refresh the resource version or ETag, then retry with the current precondition.",
            429: "Respect Retry-After, use exponential backoff with jitter, and reduce burst concurrency.",
        }
        if code in resolutions:
            return AgentDecision(
                action=DecisionAction.RESOLVE,
                rationale="The request inspection confirms a standard client-remediable condition.",
                resolution=resolutions[int(code)],
                confidence=0.9,
                **base,
            )
        return AgentDecision(
            action=DecisionAction.ESCALATE,
            rationale="The inspected API request has no safe standard resolution.",
            escalation_reason="Request evidence is insufficient for a confident standard resolution.",
            confidence=0.55,
            **base,
        )

    def _configuration_or_data_decision(
        self,
        state: CaseState,
        category: IssueCategory,
        severity: Severity,
        impact: str | None,
    ) -> AgentDecision:
        if not state.tool_was_called("search_internal_docs"):
            return AgentDecision(
                action=DecisionAction.CALL_TOOL,
                rationale="Search the runbook for a standard diagnostic before escalating an underspecified case.",
                issue_category=category,
                severity=severity,
                customer_impact=impact,
                tool_name="search_internal_docs",
                tool_arguments={"query": "configuration data validation troubleshooting"},
                confidence=0.55,
            )
        return AgentDecision(
            action=DecisionAction.ASK_CUSTOMER,
            rationale="The runbook alone cannot identify the affected object or configuration.",
            issue_category=category,
            severity=severity,
            customer_impact=impact,
            questions=[
                "Which object, field, or configuration changed, and what value did you expect versus observe?",
                "Can you provide the relevant request or event ID and timestamp?",
            ],
            missing_information=["affected_object", "expected_vs_observed", "request_id_or_event_id"],
            confidence=0.4,
        )


def build_adapter(provider: str | None = None) -> DecisionAdapter:
    """Build the selected adapter; mock is the safe default."""

    selected = (provider or os.getenv("SUPPORT_TRIAGE_LLM", "mock")).strip().lower()
    if selected == "mock":
        return MockDecisionAdapter()
    if selected == "openai":
        return OpenAIResponsesAdapter()
    raise ValueError(f"Unknown decision provider: {selected!r}. Choose 'mock' or 'openai'.")
