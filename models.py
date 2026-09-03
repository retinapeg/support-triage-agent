"""Shared domain models for the support-triage agent.

The models deliberately separate confirmed evidence from hypotheses.  That
distinction is one of the most important guardrails in a support workflow:
observations may justify a theory, but a theory must never silently become a
log entry or a customer fact.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    """Return a timezone-aware processing timestamp."""

    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    """Base model that rejects misspelled or unexpected fields."""

    model_config = ConfigDict(extra="forbid")


class IssueCategory(str, Enum):
    AUTHENTICATION = "authentication"
    API_REQUEST = "api_request"
    WEBHOOK = "webhook"
    DATA = "data"
    CONFIGURATION = "configuration"
    RATE_LIMITING = "rate_limiting"
    CONFLICT_STATE = "conflict_state"
    UNKNOWN = "unknown"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CaseStatus(str, Enum):
    NEW = "new"
    INVESTIGATING = "investigating"
    AWAITING_CUSTOMER = "awaiting_customer"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class DecisionAction(str, Enum):
    ASK_CUSTOMER = "ask_customer"
    CALL_TOOL = "call_tool"
    RESOLVE = "resolve"
    ESCALATE = "escalate"


class EventType(str, Enum):
    CUSTOMER_INPUT = "customer_input"
    DECISION = "decision"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    STATE_UPDATE = "state_update"
    GUARDRAIL = "guardrail"
    STOP = "stop"


class HttpStatusContext(str, Enum):
    CUSTOMER_REPORT = "customer_report"
    API_REQUEST = "api_request"
    WEBHOOK_ENDPOINT = "webhook_endpoint"
    REFERENCE = "reference"


class Evidence(StrictModel):
    """A fact observed from a named source, never an inference."""

    source: str
    fact: str
    observed_at: str | None = None
    identifiers: dict[str, str] = Field(default_factory=dict)


class HttpStatusObservation(StrictModel):
    """An HTTP status with its layer preserved to avoid false correlation."""

    code: int = Field(ge=100, le=599)
    context: HttpStatusContext
    source: str
    identifier: str | None = None


class Hypothesis(StrictModel):
    """A possible explanation that remains explicitly unconfirmed."""

    statement: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    status: str = Field(default="open", pattern=r"^(open|supported|refuted|confirmed)$")
    basis: list[str] = Field(default_factory=list)


class ToolResult(StrictModel):
    """Normalised result envelope returned by every support tool."""

    tool_name: str
    success: bool
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    error: str | None = None


class AgentDecision(StrictModel):
    """One model-selected action in the outer agent loop."""

    action: DecisionAction
    rationale: str
    issue_category: IssueCategory | None = None
    issue_categories: list[IssueCategory] = Field(default_factory=list)
    severity: Severity | None = None
    customer_impact: str | None = None
    questions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    tool_name: str | None = None
    tool_arguments: dict[str, Any] = Field(default_factory=dict)
    hypotheses: list[str] = Field(default_factory=list)
    resolution: str | None = None
    escalation_reason: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_action_payload(self) -> "AgentDecision":
        if self.action == DecisionAction.ASK_CUSTOMER and not self.questions:
            raise ValueError("ask_customer decisions require at least one question")
        if self.action == DecisionAction.CALL_TOOL and not self.tool_name:
            raise ValueError("call_tool decisions require tool_name")
        if self.action == DecisionAction.RESOLVE and not self.resolution:
            raise ValueError("resolve decisions require resolution")
        if self.action == DecisionAction.ESCALATE and not self.escalation_reason:
            raise ValueError("escalate decisions require escalation_reason")
        return self


class ActionRecord(StrictModel):
    """Compact, human-readable list of decisions and work performed."""

    step: int
    action: str
    summary: str
    tool_name: str | None = None
    processing_timestamp: datetime = Field(default_factory=utc_now)


class AuditEvent(StrictModel):
    """Detailed append-only observability record."""

    step: int
    event_type: EventType
    processing_timestamp: datetime = Field(default_factory=utc_now)
    message: str | None = None
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)


class EngineeringEscalation(StrictModel):
    """Structured hand-off contract for engineering."""

    case_id: str
    customer: str
    severity: Severity
    escalation_reason: str
    customer_impact: str
    symptoms: list[str]
    timestamps: list[str]
    request_ids: list[str]
    http_status_codes: list[int]
    webhook_ids: list[str]
    relevant_details: dict[str, Any]
    troubleshooting_performed: list[str]
    likely_root_cause: str
    outstanding_questions: list[str]


TERMINAL_STATUSES = {CaseStatus.RESOLVED, CaseStatus.ESCALATED}
