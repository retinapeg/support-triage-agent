"""Case state and guarded state-transition helpers."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable, Mapping
from uuid import uuid4

from pydantic import Field

from models import (
    ActionRecord,
    AgentDecision,
    AuditEvent,
    CaseStatus,
    EngineeringEscalation,
    EventType,
    Evidence,
    Hypothesis,
    HttpStatusContext,
    HttpStatusObservation,
    IssueCategory,
    Severity,
    StrictModel,
    ToolResult,
    utc_now,
)


REQUEST_ID_RE = re.compile(r"\breq[-_][A-Za-z0-9][A-Za-z0-9_-]*\b", re.IGNORECASE)
WEBHOOK_ID_RE = re.compile(r"\b(?:evt|event)[-_][A-Za-z0-9][A-Za-z0-9_-]*\b", re.IGNORECASE)
API_KEY_ID_RE = re.compile(r"\b(?:key|api_key)[-_][A-Za-z0-9][A-Za-z0-9_-]*\b", re.IGNORECASE)
HTTP_CODE_RE = re.compile(r"(?<!\d)([1-5]\d{2})(?!\d)")
ISO_TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b",
    re.IGNORECASE,
)
TIME_TIMESTAMP_RE = re.compile(r"\b\d{1,2}:\d{2}(?:\s?(?:UTC|GMT|BST))\b", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)


def _append_unique(target: list[Any], values: Iterable[Any]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


class CaseState(StrictModel):
    case_id: str
    original_message: str
    customer: str | None = None
    issue_category: IssueCategory = IssueCategory.UNKNOWN
    related_issue_categories: list[IssueCategory] = Field(default_factory=list)
    severity: Severity = Severity.MEDIUM
    customer_impact: str | None = None

    # These are incident timestamps supplied by the customer or returned by a
    # tool. Processing timestamps live separately on AuditEvent.
    timestamps: list[str] = Field(default_factory=list)
    request_ids: list[str] = Field(default_factory=list)
    http_codes: list[int] = Field(default_factory=list)
    http_status_observations: list[HttpStatusObservation] = Field(default_factory=list)
    webhook_ids: list[str] = Field(default_factory=list)
    api_key_ids: list[str] = Field(default_factory=list)
    endpoints: list[str] = Field(default_factory=list)

    evidence: list[Evidence] = Field(default_factory=list)
    actions_taken: list[ActionRecord] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)

    next_action: str | None = None
    escalation_required: bool = False
    resolution: str | None = None
    escalation: EngineeringEscalation | None = None
    status: CaseStatus = CaseStatus.NEW
    stop_reason: str | None = None

    step_count: int = 0
    max_steps: int = Field(default=8, ge=1, le=30)
    customer_messages: list[str] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    audit_trail: list[AuditEvent] = Field(default_factory=list)
    processing_started_at: datetime = Field(default_factory=utc_now)

    @classmethod
    def create(
        cls,
        original_message: str,
        *,
        customer: str | None = None,
        max_steps: int = 8,
        case_id: str | None = None,
    ) -> "CaseState":
        if not original_message.strip():
            raise ValueError("original_message must not be blank")
        state = cls(
            case_id=case_id or f"case_{uuid4().hex[:12]}",
            original_message=original_message.strip(),
            customer=customer,
            max_steps=max_steps,
        )
        state.add_customer_message(original_message, is_original=True)
        return state

    def add_customer_message(self, message: str, *, is_original: bool = False) -> None:
        """Append customer input and extract only identifiers literally present."""

        clean = message.strip()
        if not clean:
            raise ValueError("customer message must not be blank")
        self.customer_messages.append(clean)
        self._extract_confirmed_values(clean)
        self._clear_satisfied_missing_information()
        if not is_original:
            self.status = CaseStatus.INVESTIGATING
            self.stop_reason = None
            self.clarification_questions = []
            self.next_action = "Reassess case with the new customer information"
        self.audit_trail.append(
            AuditEvent(
                step=self.step_count,
                event_type=EventType.CUSTOMER_INPUT,
                message=clean,
            )
        )

    def _extract_confirmed_values(self, text: str) -> None:
        _append_unique(self.request_ids, (item.lower() for item in REQUEST_ID_RE.findall(text)))
        _append_unique(self.webhook_ids, (item.lower() for item in WEBHOOK_ID_RE.findall(text)))
        _append_unique(self.api_key_ids, (item.lower() for item in API_KEY_ID_RE.findall(text)))
        # Mask identifiers and URLs before looking for HTTP codes. Otherwise a
        # fixture such as ``evt_endpoint_500`` would create a fake HTTP 500
        # observation even when the customer never reported a response code.
        status_text = REQUEST_ID_RE.sub(" ", text)
        status_text = WEBHOOK_ID_RE.sub(" ", status_text)
        status_text = API_KEY_ID_RE.sub(" ", status_text)
        status_text = URL_RE.sub(" ", status_text)
        customer_codes = [int(item) for item in HTTP_CODE_RE.findall(status_text)]
        _append_unique(self.http_codes, customer_codes)
        text_lower = text.lower()
        if "webhook" in text_lower and not any(term in text_lower for term in ("api", "request")):
            context = HttpStatusContext.WEBHOOK_ENDPOINT
            identifier = self.webhook_ids[-1] if self.webhook_ids else None
        elif any(term in text_lower for term in ("api", "request", "401", "403", "429")):
            context = HttpStatusContext.API_REQUEST
            identifier = self.request_ids[-1] if self.request_ids else None
        else:
            context = HttpStatusContext.CUSTOMER_REPORT
            identifier = None
        _append_unique(
            self.http_status_observations,
            (
                HttpStatusObservation(
                    code=code,
                    context=context,
                    source="customer_message",
                    identifier=identifier,
                )
                for code in customer_codes
            ),
        )
        _append_unique(self.timestamps, ISO_TIMESTAMP_RE.findall(text))
        _append_unique(self.timestamps, TIME_TIMESTAMP_RE.findall(text))
        _append_unique(self.endpoints, (url.rstrip(".,);") for url in URL_RE.findall(text)))

    def _clear_satisfied_missing_information(self) -> None:
        availability = {
            "request_id": bool(self.request_ids),
            "api_key_id": bool(self.api_key_ids),
            "event_id": bool(self.webhook_ids),
            "webhook_id": bool(self.webhook_ids),
            "timestamp": bool(self.timestamps),
            "endpoint": bool(self.endpoints),
            "customer_impact": bool(self.customer_impact),
            "request_id_or_event_id": bool(self.request_ids or self.webhook_ids),
            "error": bool(self.http_codes) or any(result.error for result in self.tool_results),
            "verified_identifier": any(
                result.success
                and result.tool_name
                in {"inspect_api_request", "check_authentication", "inspect_webhook_delivery"}
                for result in self.tool_results
            ),
        }
        self.missing_information = [
            item for item in self.missing_information if not availability.get(item, False)
        ]

    def begin_step(self, decision: AgentDecision) -> None:
        self.step_count += 1
        self.status = CaseStatus.INVESTIGATING
        self.next_action = decision.action.value
        if decision.issue_category is not None:
            self.issue_category = decision.issue_category
            _append_unique(self.related_issue_categories, [decision.issue_category])
        _append_unique(self.related_issue_categories, decision.issue_categories)
        if decision.severity is not None:
            self.severity = decision.severity
        rejected_impact: str | None = None
        if decision.customer_impact:
            candidate = re.sub(r"\s+", " ", decision.customer_impact).strip(" .")
            customer_corpus = re.sub(r"\s+", " ", " ".join(self.customer_messages)).lower()
            if candidate.lower() in customer_corpus:
                self.customer_impact = candidate
            else:
                rejected_impact = candidate
        _append_unique(self.missing_information, decision.missing_information)
        for statement in decision.hypotheses:
            if not any(existing.statement == statement for existing in self.hypotheses):
                self.hypotheses.append(
                    Hypothesis(statement=statement, confidence=decision.confidence)
                )
        self.actions_taken.append(
            ActionRecord(
                step=self.step_count,
                action=decision.action.value,
                summary=decision.rationale,
                tool_name=decision.tool_name,
            )
        )
        self.audit_trail.append(
            AuditEvent(
                step=self.step_count,
                event_type=EventType.DECISION,
                message=decision.rationale,
                tool_name=decision.tool_name,
                arguments=decision.tool_arguments,
            )
        )
        if rejected_impact:
            self.audit_trail.append(
                AuditEvent(
                    step=self.step_count,
                    event_type=EventType.GUARDRAIL,
                    message=(
                        "Customer-impact update rejected because it was not a literal claim in "
                        "customer-provided case text."
                    ),
                    result={"rejected_customer_impact": rejected_impact},
                )
            )
        self._clear_satisfied_missing_information()

    def record_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> None:
        self.audit_trail.append(
            AuditEvent(
                step=self.step_count,
                event_type=EventType.TOOL_CALL,
                tool_name=tool_name,
                arguments=arguments,
            )
        )

    def record_tool_result(self, result: ToolResult) -> None:
        self.tool_results.append(result)
        for item in result.evidence:
            if item not in self.evidence:
                self.evidence.append(item)
        # Extract only values under explicit provenance-bearing field names.
        # Regexing serialised JSON would mistake keys such as ``event_id`` and
        # ``event_type`` for customer identifiers.
        if result.success and result.data.get("found") is not False:
            self._extract_confirmed_structure(result.data)
            for item in result.evidence:
                self._extract_confirmed_structure(item.identifiers)
            self._record_scoped_http_status(result)
        self._clear_satisfied_missing_information()
        self.audit_trail.append(
            AuditEvent(
                step=self.step_count,
                event_type=EventType.TOOL_RESULT,
                tool_name=result.tool_name,
                message=result.summary,
                result=result.model_dump(mode="json"),
            )
        )
        self.audit_trail.append(
            AuditEvent(
                step=self.step_count,
                event_type=EventType.STATE_UPDATE,
                message=(
                    f"Case state now contains {len(self.evidence)} confirmed evidence item(s), "
                    f"{len(self.request_ids)} request ID(s), {len(self.webhook_ids)} event ID(s), "
                    f"and {len(self.http_codes)} HTTP status code(s)."
                ),
                result={
                    "evidence_count": len(self.evidence),
                    "request_ids": list(self.request_ids),
                    "webhook_ids": list(self.webhook_ids),
                    "http_codes": list(self.http_codes),
                    "missing_information": list(self.missing_information),
                },
            )
        )

    def _record_scoped_http_status(self, result: ToolResult) -> None:
        code: int | None = None
        context: HttpStatusContext | None = None
        identifier: str | None = None
        if result.tool_name == "inspect_api_request":
            raw_code = result.data.get("status_code")
            context = HttpStatusContext.API_REQUEST
            request = result.data.get("request", {})
            identifier = request.get("request_id") if isinstance(request, Mapping) else None
        elif result.tool_name == "inspect_webhook_delivery":
            raw_code = result.data.get("response_code")
            context = HttpStatusContext.WEBHOOK_ENDPOINT
            delivery = result.data.get("webhook_delivery", {})
            identifier = delivery.get("event_id") if isinstance(delivery, Mapping) else None
        elif result.tool_name == "lookup_http_status":
            status = result.data.get("status", {})
            raw_code = status.get("code") if isinstance(status, Mapping) else None
            context = HttpStatusContext.REFERENCE
        else:
            return
        try:
            code = int(raw_code) if raw_code is not None else None
        except (TypeError, ValueError):
            return
        if code is not None and context is not None and 100 <= code <= 599:
            _append_unique(
                self.http_status_observations,
                [
                    HttpStatusObservation(
                        code=code,
                        context=context,
                        source=result.tool_name,
                        identifier=identifier,
                    )
                ],
            )
    def _extract_confirmed_structure(self, value: Any) -> None:
        if isinstance(value, Mapping):
            for raw_key, item in value.items():
                key = str(raw_key).lower()
                if key == "request_id" and isinstance(item, str) and REQUEST_ID_RE.fullmatch(item):
                    _append_unique(self.request_ids, [item.lower()])
                elif key in {"event_id", "webhook_id"} and isinstance(item, str) and WEBHOOK_ID_RE.fullmatch(item):
                    _append_unique(self.webhook_ids, [item.lower()])
                elif key == "api_key_id" and isinstance(item, str) and API_KEY_ID_RE.fullmatch(item):
                    _append_unique(self.api_key_ids, [item.lower()])
                elif key in {"status_code", "http_status", "response_status", "response_code"}:
                    try:
                        code = int(item)
                    except (TypeError, ValueError):
                        pass
                    else:
                        if HTTP_CODE_RE.fullmatch(str(code)):
                            _append_unique(self.http_codes, [code])
                elif key in {"timestamp", "occurred_at", "attempted_at", "observed_at"} and isinstance(item, str):
                    _append_unique(self.timestamps, ISO_TIMESTAMP_RE.findall(item))
                    _append_unique(self.timestamps, TIME_TIMESTAMP_RE.findall(item))
                elif key == "endpoint" and isinstance(item, str):
                    _append_unique(self.endpoints, (url.rstrip(".,);") for url in URL_RE.findall(item)))
                self._extract_confirmed_structure(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                self._extract_confirmed_structure(item)

    def record_guardrail(self, message: str, *, tool_name: str | None = None) -> None:
        self.audit_trail.append(
            AuditEvent(
                step=self.step_count,
                event_type=EventType.GUARDRAIL,
                tool_name=tool_name,
                message=message,
            )
        )

    def tool_was_called(self, tool_name: str) -> bool:
        return any(result.tool_name == tool_name for result in self.tool_results)

    def latest_tool_result(self, tool_name: str) -> ToolResult | None:
        for result in reversed(self.tool_results):
            if result.tool_name == tool_name:
                return result
        return None

    def stop(self, *, reason: str, status: CaseStatus) -> None:
        self.status = status
        self.stop_reason = reason
        self.next_action = None
        self.audit_trail.append(
            AuditEvent(
                step=self.step_count,
                event_type=EventType.STOP,
                message=reason,
            )
        )
