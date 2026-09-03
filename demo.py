"""Run three end-to-end Support Triage Agent scenarios in mock or live mode."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass

from agent import SupportTriageAgent
from llm import build_adapter
from models import CaseStatus, EventType
from state import CaseState


@dataclass(frozen=True)
class Scenario:
    number: int
    title: str
    issue: str
    customer: str
    followups: tuple[str, ...] = ()


SCENARIOS = (
    Scenario(
        number=1,
        title="Intermittent 401 authentication failures",
        customer="Northstar Payments (synthetic)",
        issue=(
            "Our API integration stopped working this morning. We are getting "
            "intermittent 401s in production."
        ),
        followups=(
            "One failure was request req_401_expired at 2026-09-03T08:15:00Z. "
            "It used the non-secret key ID key_expired. Production checkout requests "
            "are failing for some users.",
        ),
    ),
    Scenario(
        number=2,
        title="Webhook generated, destination endpoint returns 500",
        customer="Riverbank Demo (synthetic)",
        issue="Our webhook isn't firing.",
        followups=(
            "Please trace event evt_endpoint_500. It should reach "
            "https://hooks.customer.example/evaluations and failed around "
            "2026-09-03T09:05:00Z. Payment status updates are delayed.",
        ),
    ),
    Scenario(
        number=3,
        title="Correlated 500 and webhook failure requiring escalation",
        customer="Aperture Finance (synthetic)",
        issue=(
            "Since 2026-09-03T08:30:00Z, production request req_500_internal returns "
            "500 and webhook event evt_endpoint_timeout is not arriving in the same incident window. Customers "
            "cannot complete payments."
        ),
    ),
)


def _print_new_events(state: CaseState, cursor: int) -> int:
    for event in state.audit_trail[cursor:]:
        prefix = f"  step {event.step:>2} | {event.event_type.value:<14}"
        if event.event_type == EventType.DECISION:
            print(f"{prefix} | {event.message}")
        elif event.event_type == EventType.TOOL_CALL:
            args = json.dumps(event.arguments, sort_keys=True)
            print(f"{prefix} | {event.tool_name}({args})")
        elif event.event_type == EventType.TOOL_RESULT:
            print(f"{prefix} | {event.tool_name}: {event.message}")
        elif event.event_type == EventType.CUSTOMER_INPUT:
            print(f"{prefix} | customer evidence received")
        else:
            print(f"{prefix} | {event.message or ''}")
    return len(state.audit_trail)


def run_scenario(scenario: Scenario, *, provider: str = "mock") -> CaseState:
    print("\n" + "=" * 88)
    print(f"SCENARIO {scenario.number}: {scenario.title}")
    print("=" * 88)
    print(f"Customer: {scenario.issue}")

    agent = SupportTriageAgent(build_adapter(provider), max_steps=8)
    state = agent.run(scenario.issue, customer=scenario.customer)
    cursor = _print_new_events(state, 0)

    for followup in scenario.followups:
        if state.status != CaseStatus.AWAITING_CUSTOMER:
            break
        print("\n  Agent asks:")
        for question in state.clarification_questions:
            print(f"    - {question}")
        print(f"\n  Customer replies: {followup}")
        state = agent.resume(state, followup)
        cursor = _print_new_events(state, cursor)

    print("\n  FINAL CASE STATE")
    print(f"    case_id:   {state.case_id}")
    print(f"    category:  {state.issue_category.value}")
    print(f"    severity:  {state.severity.value}")
    print(f"    status:    {state.status.value}")
    print(f"    steps:     {state.step_count}/{state.max_steps}")
    if state.resolution:
        print(f"    resolution: {state.resolution}")
    if state.escalation:
        print("    engineering escalation:")
        print(json.dumps(state.escalation.model_dump(mode="json"), indent=6))
    elif state.status == CaseStatus.AWAITING_CUSTOMER:
        print("    waiting for customer clarification")
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        choices=("all", "1", "2", "3"),
        default="all",
        help="Run all scenarios or one numbered scenario (default: all).",
    )
    parser.add_argument(
        "--provider",
        choices=("mock", "openai"),
        default=None,
        help="Decision provider; defaults to SUPPORT_TRIAGE_LLM or mock.",
    )
    args = parser.parse_args()
    provider = args.provider or "mock"
    selected = SCENARIOS if args.scenario == "all" else (SCENARIOS[int(args.scenario) - 1],)
    for scenario in selected:
        run_scenario(scenario, provider=provider)


if __name__ == "__main__":
    main()
