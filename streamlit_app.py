"""Presenter-friendly UI for the Support Triage Agent portfolio project.

The interface is deliberately a view over the same agent used by ``demo.py``.
It does not contain a second troubleshooting implementation, and every bundled
case, identifier, log line, and customer name is synthetic.
"""

from __future__ import annotations

import json
import os
from html import escape
from textwrap import dedent
from typing import Any

import streamlit as st

from agent import SupportTriageAgent
from demo import SCENARIOS, Scenario
from llm import build_adapter
from models import CaseStatus, EventType
from state import CaseState


st.set_page_config(
    page_title="Triage & Discovery Console",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)


SCENARIO_PRESENTATION: dict[int, dict[str, str]] = {
    1: {
        "eyebrow": "AUTHENTICATION",
        "title": "An ambiguous intermittent 401",
        "description": (
            "Starts with a vague production report, pauses for the exact identifiers, "
            "then checks credential and request evidence before recommending action."
        ),
        "proof": "Targeted discovery → evidence-backed resolution",
    },
    2: {
        "eyebrow": "WEBHOOK BOUNDARY",
        "title": "“Our webhook isn’t firing”",
        "description": (
            "Requests an event ID and timing, then separates event generation, delivery "
            "attempts, and the customer endpoint response."
        ),
        "proof": "Boundary isolation → safe next step",
    },
    3: {
        "eyebrow": "ENGINEERING HANDOFF",
        "title": "API 500 plus webhook timeout",
        "description": (
            "Correlates two high-impact symptoms without inventing a shared root cause, "
            "then creates an investigation-ready escalation."
        ),
        "proof": "Disciplined triage → useful escalation",
    },
}


SCENARIO_CONTROL_LABELS = {
    1: "1 · Authentication 401",
    2: "2 · Webhook delivery",
    3: "3 · API + webhook handoff",
}


EVENT_PRESENTATION = {
    EventType.CUSTOMER_INPUT: ("Customer evidence", "💬"),
    EventType.DECISION: ("Triage decision", "◇"),
    EventType.TOOL_CALL: ("Diagnostic requested", "⚙"),
    EventType.TOOL_RESULT: ("Observation returned", "◎"),
    EventType.STATE_UPDATE: ("Case state updated", "↻"),
    EventType.GUARDRAIL: ("Guardrail intervened", "🛡"),
    EventType.STOP: ("Explicit stop", "■"),
}


TOOL_LABELS = {
    "inspect_api_request": "Inspect API request",
    "check_authentication": "Check credential metadata",
    "inspect_webhook_delivery": "Trace webhook delivery",
    "check_service_status": "Check service status",
    "lookup_http_status": "Interpret HTTP status",
    "search_internal_docs": "Search support reference",
    "create_engineering_escalation": "Build engineering handoff",
}


STATUS_LABELS = {
    CaseStatus.NEW: "New case",
    CaseStatus.INVESTIGATING: "Investigating",
    CaseStatus.AWAITING_CUSTOMER: "Clarification required",
    CaseStatus.RESOLVED: "Evidence-supported resolution",
    CaseStatus.ESCALATED: "Handoff required",
}


def _apply_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --ink: #17223b;
            --muted: #5f6b85;
            --line: #dce4f1;
            --navy: #14213d;
            --blue: #2764ff;
            --cyan: #31c4d8;
            --green: #0f9f6e;
            color-scheme: light;
        }
        .stApp {
            background:
                radial-gradient(circle at 78% 0%, rgba(49,196,216,.10), transparent 28rem),
                radial-gradient(circle at 8% 12%, rgba(39,100,255,.08), transparent 26rem),
                #f6f8fc;
        }
        [data-testid="stMain"] { color: var(--ink); }
        [data-testid="stMain"] h1,
        [data-testid="stMain"] h2,
        [data-testid="stMain"] h3,
        [data-testid="stMain"] h4,
        [data-testid="stMain"] h5,
        [data-testid="stMain"] h6,
        [data-testid="stMain"] label,
        [data-testid="stMain"] [data-testid="stMarkdownContainer"] p,
        [data-testid="stMain"] [role="tab"] p,
        [data-testid="stMain"] [data-testid="stMetric"] * {
            color: var(--ink);
        }
        [data-testid="stMain"] [data-testid="stCaptionContainer"] p { color: var(--muted); }
        [data-testid="stMain"] button p { color: inherit !important; }
        [data-testid="stMain"] button[kind="secondary"]:not(:disabled) {
            background: #ffffff !important;
            border-color: #cfd9ec !important;
            color: var(--ink) !important;
        }
        [data-testid="stMain"] button[kind="secondary"]:not(:disabled) p {
            color: var(--ink) !important;
        }
        [data-testid="stMain"] button[kind="primary"]:not(:disabled) {
            background: var(--blue) !important;
            border-color: var(--blue) !important;
            color: #ffffff !important;
        }
        [data-testid="stMain"] button[kind="primary"]:not(:disabled) p {
            color: #ffffff !important;
        }
        [data-testid="stMain"] button:disabled {
            background: #e9eef8 !important;
            border-color: #cfd9ec !important;
            color: #52617f !important;
            opacity: 1 !important;
        }
        [data-testid="stMain"] button:disabled p { color: #52617f !important; }
        [data-testid="stMain"] button[kind="primary"]:disabled {
            background: #dce7ff !important;
            border-color: #9db6f8 !important;
            color: #234caa !important;
        }
        [data-testid="stMain"] button[kind="primary"]:disabled p { color: #234caa !important; }
        .block-container { max-width: 1320px; padding-top: 1.35rem; padding-bottom: 4rem; }
        [data-testid="stSidebar"] { background: #111b32; border-right: 1px solid rgba(255,255,255,.08); }
        [data-testid="stSidebar"] * { color: #eef3ff; }
        [data-testid="stSidebar"] .stAlert * { color: inherit; }
        [data-testid="stSidebar"] [data-baseweb="select"] * { color: #17223b; }
        [data-testid="stSidebar"] input { color: #17223b; }
        [data-testid="stSidebar"] [data-testid="stExpander"] {
            background: rgba(255,255,255,.045);
            border-color: rgba(255,255,255,.12);
        }
        [data-testid="stSidebar"] [data-testid="stExpander"] details,
        [data-testid="stSidebar"] [data-testid="stExpander"] summary {
            background: transparent !important;
        }

        .hero {
            position: relative;
            overflow: hidden;
            padding: 2.15rem 2.35rem 2rem;
            border-radius: 24px;
            background: linear-gradient(120deg, rgba(20,33,61,.98), rgba(28,58,113,.97) 58%, rgba(28,111,137,.94));
            color: white;
            box-shadow: 0 18px 45px rgba(20,33,61,.16);
        }
        .hero:after {
            content: "";
            position: absolute;
            width: 310px;
            height: 310px;
            right: -75px;
            top: -165px;
            border: 1px solid rgba(255,255,255,.18);
            border-radius: 50%;
            box-shadow: 0 0 0 48px rgba(255,255,255,.035), 0 0 0 96px rgba(255,255,255,.025);
        }
        .hero-eyebrow, .tiny-label {
            font-size: .72rem;
            font-weight: 800;
            letter-spacing: .14em;
            text-transform: uppercase;
        }
        .hero-eyebrow { color: #76e4ee; margin-bottom: .72rem; }
        .hero h1 { color: white; font-size: 2.42rem; line-height: 1.08; margin: 0; max-width: 830px; }
        .hero p { color: rgba(255,255,255,.82); max-width: 810px; font-size: 1.02rem; line-height: 1.62; margin: .9rem 0 1.25rem; }
        .badge-row { display: flex; gap: .55rem; flex-wrap: wrap; }
        .hero-badge {
            display: inline-block;
            padding: .34rem .65rem;
            border: 1px solid rgba(255,255,255,.20);
            border-radius: 999px;
            background: rgba(255,255,255,.08);
            color: rgba(255,255,255,.92);
            font-size: .76rem;
            font-weight: 650;
        }

        .workflow {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: .7rem;
            margin: 1.05rem 0 1.55rem;
        }
        .workflow-step {
            min-height: 76px;
            padding: .86rem 1rem;
            background: rgba(255,255,255,.78);
            border: 1px solid var(--line);
            border-radius: 15px;
        }
        .workflow-step .number {
            display: inline-grid;
            place-items: center;
            width: 25px;
            height: 25px;
            margin-right: .4rem;
            border-radius: 8px;
            background: #e9eef8;
            color: var(--muted);
            font-weight: 800;
            font-size: .75rem;
        }
        .workflow-step strong { color: var(--ink); font-size: .88rem; }
        .workflow-step small { display: block; color: var(--muted); margin: .38rem 0 0 2.15rem; line-height: 1.25; }
        .workflow-step.done { border-color: rgba(15,159,110,.28); background: rgba(237,251,246,.94); }
        .workflow-step.done .number { background: var(--green); color: white; }
        .workflow-step.active { border-color: rgba(39,100,255,.55); box-shadow: 0 0 0 3px rgba(39,100,255,.08); background: white; }
        .workflow-step.active .number { background: var(--blue); color: white; }

        .section-kicker {
            color: var(--blue);
            font-size: .72rem;
            font-weight: 850;
            letter-spacing: .13em;
            text-transform: uppercase;
            margin: 1.25rem 0 .2rem;
        }
        .case-strip {
            display: flex;
            justify-content: space-between;
            gap: 1rem;
            align-items: center;
            padding: .9rem 1.05rem;
            margin: .1rem 0 .8rem;
            border: 1px solid var(--line);
            border-radius: 14px;
            background: rgba(255,255,255,.88);
        }
        .case-strip strong { color: var(--ink); }
        .case-strip span { color: var(--muted); font-size: .82rem; }
        .status-pill {
            display: inline-block;
            padding: .36rem .72rem;
            border-radius: 999px;
            font-size: .72rem;
            font-weight: 850;
            letter-spacing: .06em;
            text-transform: uppercase;
            white-space: nowrap;
        }
        .status-awaiting_customer { color: #965900; background: #fff0cf; }
        .status-resolved { color: #087650; background: #dff8ee; }
        .status-escalated { color: #a7263c; background: #ffe3e8; }
        .status-investigating, .status-new { color: #174bbd; background: #e3ecff; }
        .case-strip .status-awaiting_customer { color: #965900; font-size: .72rem; }
        .case-strip .status-resolved { color: #087650; font-size: .72rem; }
        .case-strip .status-escalated { color: #a7263c; font-size: .72rem; }
        .case-strip .status-investigating, .case-strip .status-new { color: #174bbd; font-size: .72rem; }

        .scenario-top { min-height: 152px; }
        .scenario-title { color: var(--ink); font-size: 1.05rem; font-weight: 780; margin: .35rem 0 .48rem; }
        .scenario-copy { color: var(--muted); font-size: .88rem; line-height: 1.48; }
        .scenario-proof { color: #2854a9; font-size: .78rem; font-weight: 720; margin-top: .72rem; }
        .outcome-card { padding: 1rem 1.1rem; border-radius: 14px; margin: .65rem 0 .8rem; line-height: 1.52; }
        .outcome-card strong { display: block; margin-bottom: .32rem; }
        .outcome-resolved { background: #eafaf4; border: 1px solid #bde8d8; color: #075c41; }
        .outcome-escalated { background: #fff0f2; border: 1px solid #f3c2cc; color: #8e2639; }
        .outcome-awaiting { background: #fff7e8; border: 1px solid #eed5a6; color: #7c5000; }
        .question-card {
            display: grid;
            grid-template-columns: 30px 1fr;
            gap: .65rem;
            padding: .72rem .8rem;
            margin: .48rem 0;
            border-radius: 12px;
            background: #f7f9fd;
            border: 1px solid #e2e8f2;
            color: var(--ink);
        }
        .question-number {
            width: 27px;
            height: 27px;
            display: grid;
            place-items: center;
            border-radius: 8px;
            background: #e5edff;
            color: #2854a9;
            font-size: .75rem;
            font-weight: 800;
        }
        .signal-row { padding: .6rem 0; border-bottom: 1px solid #edf0f6; }
        .signal-row:last-child { border-bottom: 0; }
        .signal-row .signal-label { color: var(--muted); font-size: .72rem; font-weight: 750; text-transform: uppercase; letter-spacing: .06em; }
        .signal-row .signal-value { color: var(--ink); margin-top: .18rem; overflow-wrap: anywhere; }
        .timeline-heading { color: var(--ink); font-weight: 760; }
        .timeline-meta { color: var(--muted); font-size: .76rem; }
        .evidence-chip {
            display: inline-block;
            padding: .25rem .52rem;
            margin: .12rem .18rem .12rem 0;
            border-radius: 8px;
            background: #edf2ff;
            color: #294c97;
            font-size: .75rem;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        }

        .architecture {
            display: grid;
            grid-template-columns: 1fr auto 1fr auto 1fr auto 1fr;
            align-items: stretch;
            gap: .45rem;
            margin: .85rem 0 1rem;
        }
        .arch-node {
            display: grid;
            align-content: center;
            min-height: 90px;
            padding: .75rem;
            text-align: center;
            border: 1px solid var(--line);
            border-radius: 13px;
            background: white;
            color: var(--ink);
            font-weight: 750;
            font-size: .84rem;
        }
        .arch-node small { display: block; color: var(--muted); font-weight: 500; margin-top: .25rem; }
        .arch-arrow { display: grid; place-items: center; color: var(--blue); font-size: 1.1rem; font-weight: 900; }
        .role-card { min-height: 126px; }
        .role-card strong { color: var(--ink); }
        .role-card p { color: var(--muted); font-size: .85rem; line-height: 1.45; }
        .safety-line {
            padding: .75rem .85rem;
            border-left: 4px solid var(--cyan);
            border-radius: 0 10px 10px 0;
            background: #edfafd;
            color: #265361;
            font-size: .83rem;
            line-height: 1.42;
        }
        @media (max-width: 900px) {
            .workflow { grid-template-columns: repeat(2, minmax(0,1fr)); }
            .architecture { grid-template-columns: 1fr; }
            .arch-arrow { transform: rotate(90deg); }
            .hero { padding: 1.55rem 1.35rem; }
            .hero h1 { font-size: 1.85rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _human(value: Any) -> str:
    raw = value.value if hasattr(value, "value") else str(value)
    return raw.replace("_", " ").strip().title()


def _joined(values: list[Any], *, empty: str = "Not supplied") -> str:
    return ", ".join(str(value) for value in values) if values else empty


def _status_label(status: CaseStatus) -> str:
    return STATUS_LABELS[status]


def _category_label(state: CaseState) -> str:
    categories = [state.issue_category, *state.related_issue_categories]
    labels: list[str] = []
    replacements = {
        "Api Request": "API",
        "Conflict State": "Conflict",
        "Rate Limiting": "Rate limit",
    }
    for category in categories:
        label = replacements.get(_human(category), _human(category))
        if label != "Unknown" and label not in labels:
            labels.append(label)
    if state.request_ids and "API" not in labels:
        labels.append("API")
    if state.webhook_ids and "Webhook" not in labels:
        labels.append("Webhook")
    if state.tool_was_called("check_authentication") and "Authentication" not in labels:
        labels.append("Authentication")
    return " + ".join(labels) if labels else "Unknown"


def _scenario(number: int | None) -> Scenario | None:
    if number is None:
        return None
    return next((item for item in SCENARIOS if item.number == number), None)


def _reset_case() -> None:
    for key in (
        "case_state",
        "active_scenario",
        "manual_reply",
        "custom_issue",
        "custom_customer",
        "case_provider",
    ):
        st.session_state.pop(key, None)


def _start_case(
    issue: str,
    customer: str | None,
    *,
    provider: str,
    max_steps: int,
    scenario_number: int | None,
) -> None:
    if not issue.strip():
        st.error("Add a customer report before starting triage.")
        return
    try:
        with st.spinner("Running the first triage pass…"):
            agent = SupportTriageAgent(build_adapter(provider), max_steps=max_steps)
            state = agent.run(issue, customer=customer or None)
        st.session_state.pop("manual_reply", None)
        st.session_state.case_state = state
        st.session_state.active_scenario = scenario_number
        st.session_state.case_provider = provider
        st.rerun()
    except Exception as exc:  # UI boundary: keep the demo usable if an adapter fails.
        st.error(f"The triage pass could not start: {exc}")


def _start_guided_scenario(number: int, *, provider: str, max_steps: int) -> None:
    scenario = _scenario(number)
    if scenario is None:
        st.error("That guided scenario is not available.")
        return
    _start_case(
        scenario.issue,
        scenario.customer,
        provider=provider,
        max_steps=max_steps,
        scenario_number=scenario.number,
    )


def _resume_case(state: CaseState, reply: str, *, provider: str) -> None:
    if not reply.strip():
        st.error("Add the customer’s reply before continuing.")
        return
    try:
        with st.spinner("Adding the new evidence and continuing the investigation…"):
            agent = SupportTriageAgent(build_adapter(provider), max_steps=state.max_steps)
            st.session_state.case_state = agent.resume(state, reply)
        st.rerun()
    except Exception as exc:  # UI boundary: surface a clear presenter-facing failure.
        st.error(f"The case could not continue: {exc}")


def _render_hero() -> None:
    st.markdown(
        """
        <div class="hero">
          <div class="hero-eyebrow">Technical Support Engineer · Triage &amp; Discovery</div>
          <h1>From “the API is broken” to the next defensible action.</h1>
          <p>
            A visual support console that turns an incomplete customer report into targeted
            discovery, evidence-led diagnosis, a safe standard resolution, or an
            investigation-ready engineering handoff.
          </p>
          <div class="badge-row">
            <span class="hero-badge">Synthetic portfolio demo</span>
            <span class="hero-badge">Deterministic mock mode</span>
            <span class="hero-badge">Synthetic inputs only</span>
            <span class="hero-badge">Human-reviewed outcomes</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _workflow_states(state: CaseState | None) -> list[str]:
    if state is None:
        return ["active", "upcoming", "upcoming", "upcoming"]
    if state.status == CaseStatus.AWAITING_CUSTOMER:
        return ["done", "active", "upcoming", "upcoming"]
    if state.status in {CaseStatus.RESOLVED, CaseStatus.ESCALATED}:
        return ["done", "done", "done", "done"]
    if state.tool_results:
        return ["done", "done", "active", "upcoming"]
    return ["done", "active", "upcoming", "upcoming"]


def _render_workflow(state: CaseState | None) -> None:
    stages = (
        ("1", "Intake", "Impact, scope and customer signal"),
        ("2", "Discover", "Ask only what separates hypotheses"),
        ("3", "Diagnose", "Inspect evidence and isolate boundary"),
        ("4", "Resolve or hand off", "Own the next action explicitly"),
    )
    cards = []
    for (number, title, detail), status in zip(stages, _workflow_states(state), strict=True):
        cards.append(
            dedent(
                f"""
            <div class="workflow-step {status}">
              <span class="number">{number}</span><strong>{title}</strong>
              <small>{detail}</small>
            </div>
            """
            ).strip()
        )
    st.markdown(f'<div class="workflow">{"".join(cards)}</div>', unsafe_allow_html=True)


def _render_scenario_switcher(state: CaseState, provider: str) -> None:
    active_scenario = _scenario(st.session_state.get("active_scenario"))
    st.markdown('<div class="section-kicker">Interactive demo controls</div>', unsafe_allow_html=True)
    with st.container(border=True):
        heading, context = st.columns([0.58, 0.42], vertical_alignment="center")
        with heading:
            st.markdown("#### Switch scenario at any time")
        with context:
            if active_scenario is None:
                st.caption("Current case: custom synthetic report")
            else:
                st.caption(
                    f"Current case: {SCENARIO_CONTROL_LABELS[active_scenario.number]}"
                )

        scenario_columns = st.columns(3)
        for column, scenario in zip(scenario_columns, SCENARIOS, strict=True):
            is_active = active_scenario is not None and scenario.number == active_scenario.number
            label = SCENARIO_CONTROL_LABELS[scenario.number]
            if is_active:
                label = f"● {label} · Active"
            with column:
                if st.button(
                    label,
                    key=f"switch_scenario_{scenario.number}",
                    type="primary" if is_active else "secondary",
                    disabled=is_active,
                    width="stretch",
                ):
                    _start_guided_scenario(
                        scenario.number,
                        provider=provider,
                        max_steps=state.max_steps,
                    )

        ordered_numbers = [scenario.number for scenario in SCENARIOS]
        active_index = (
            ordered_numbers.index(active_scenario.number)
            if active_scenario is not None
            else None
        )
        previous_number = (
            ordered_numbers[active_index - 1]
            if active_index is not None and active_index > 0
            else None
        )
        next_number = (
            ordered_numbers[active_index + 1]
            if active_index is not None and active_index < len(ordered_numbers) - 1
            else None
        )

        previous, rerun, next_case = st.columns([1, 1.25, 1])
        with previous:
            if st.button(
                "← Previous case",
                key="scenario_previous",
                disabled=previous_number is None,
                width="stretch",
            ):
                _start_guided_scenario(
                    previous_number,
                    provider=provider,
                    max_steps=state.max_steps,
                )
        with rerun:
            if st.button(
                "↻ Re-run current case",
                key="scenario_rerun",
                width="stretch",
            ):
                if active_scenario is not None:
                    _start_guided_scenario(
                        active_scenario.number,
                        provider=provider,
                        max_steps=state.max_steps,
                    )
                else:
                    _start_case(
                        state.original_message,
                        state.customer,
                        provider=provider,
                        max_steps=state.max_steps,
                        scenario_number=None,
                    )
        with next_case:
            if st.button(
                "Next case →",
                key="scenario_next",
                disabled=next_number is None,
                width="stretch",
            ):
                _start_guided_scenario(
                    next_number,
                    provider=provider,
                    max_steps=state.max_steps,
                )

        st.caption(
            "Switching or re-running creates a fresh synthetic case. The three bundled scenario stories, fixtures and outcomes are unchanged."
        )


def _render_sidebar() -> tuple[str, int]:
    with st.sidebar:
        st.markdown("### ◈ Triage console")
        st.caption("A portfolio interface over the same typed Python agent used by the CLI demo.")
        st.divider()
        provider = st.selectbox(
            "Decision mode",
            ("mock", "openai"),
            format_func=lambda value: "Mock · repeatable" if value == "mock" else "OpenAI · optional",
            help="Mock mode needs no API key and is the recommended interview demo.",
            disabled="case_state" in st.session_state,
        )
        max_steps = st.slider(
            "Safety step limit",
            min_value=2,
            max_value=12,
            value=8,
            disabled="case_state" in st.session_state,
            help="The loop stops or escalates when this external budget is exhausted.",
        )
        if provider == "mock":
            st.success("Ready — no credentials required")
        elif not os.getenv("OPENAI_API_KEY"):
            st.warning("OPENAI_API_KEY is not set. Use mock mode for the demo.")
        else:
            st.warning(
                "Provider notice: OpenAI mode sends the entered case content to the configured model. "
                "Use synthetic content only; usage may incur cost."
            )
        if "case_state" in st.session_state:
            if st.button(
                "← Scenario gallery or custom case",
                key="clear_case_to_gallery",
                width="stretch",
            ):
                _reset_case()
                st.rerun()
        st.divider()
        with st.expander("Presenter cues", expanded=True):
            st.markdown(
                """
                1. Start **401 authentication**.
                2. Point out the safe stop and targeted questions.
                3. Add the sample reply and show the evidence trail.
                4. Run **engineering handoff** to show escalation judgement.
                """
            )
        with st.expander("What this demonstrates"):
            st.markdown(
                """
                - A substantive first response
                - Evidence and hypotheses kept separate
                - API, authentication and webhook boundary isolation
                - Explicit resolve-versus-escalate judgement
                - A senior engineer can continue without restarting discovery
                """
            )
        st.markdown(
            """
            <div class="safety-line">
              <strong>Scope:</strong> synthetic fixtures only. This is not connected to Alloy,
              Zendesk, a bank, or any live customer system. Do not enter real customer information,
              credentials, access tokens, API secrets, or PII.
            </div>
            """,
            unsafe_allow_html=True,
        )
    return provider, max_steps


def _render_landing(provider: str, max_steps: int) -> None:
    st.markdown('<div class="section-kicker">Guided case lab</div>', unsafe_allow_html=True)
    st.subheader("Choose the capability you want to demonstrate")
    st.caption(
        "The first two cases intentionally pause for missing evidence. That stop is part of the design: the agent does not invent identifiers or logs."
    )
    columns = st.columns(3)
    for column, scenario in zip(columns, SCENARIOS, strict=True):
        presentation = SCENARIO_PRESENTATION[scenario.number]
        with column:
            with st.container(border=True):
                st.markdown(
                    f"""
                    <div class="scenario-top">
                      <div class="tiny-label" style="color:#2764ff">{presentation['eyebrow']}</div>
                      <div class="scenario-title">{presentation['title']}</div>
                      <div class="scenario-copy">{presentation['description']}</div>
                      <div class="scenario-proof">{presentation['proof']}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if st.button(
                    "Open guided case →",
                    key=f"start_scenario_{scenario.number}",
                    type="primary" if scenario.number == 1 else "secondary",
                    width="stretch",
                ):
                    _start_case(
                        scenario.issue,
                        scenario.customer,
                        provider=provider,
                        max_steps=max_steps,
                        scenario_number=scenario.number,
                    )

    with st.expander("Or triage your own synthetic customer report"):
        with st.form("custom_case_form"):
            customer = st.text_input(
                "Customer or organisation",
                placeholder="Example Fintech (synthetic)",
                key="custom_customer",
            )
            issue = st.text_area(
                "Customer report",
                height=145,
                placeholder=(
                    "Production request req_example returned 429 at 2026-09-03T10:15:00Z. "
                    "New applications are delayed."
                ),
                key="custom_issue",
            )
            submitted = st.form_submit_button("Start triage", type="primary")
        if submitted:
            _start_case(
                issue,
                customer or None,
                provider=provider,
                max_steps=max_steps,
                scenario_number=None,
            )
    st.markdown('<div class="section-kicker">Design principle</div>', unsafe_allow_html=True)
    left, middle, right = st.columns(3)
    principles = (
        (left, "Facts come from tools", "The decision layer can choose a check, but it cannot promote a guess into operational truth."),
        (middle, "Missing evidence causes a stop", "The case waits for a customer reply instead of fabricating a request ID or looping endlessly."),
        (right, "The handoff is a product", "Impact, identifiers, diagnostics, uncertainty and open questions travel together to the next owner."),
    )
    for column, title, copy in principles:
        with column:
            with st.container(border=True):
                st.markdown(f"**{title}**")
                st.caption(copy)


def _render_case_strip(state: CaseState) -> None:
    customer = escape(state.customer or "Unspecified customer")
    status = state.status.value
    st.markdown(
        f"""
        <div class="case-strip">
          <div>
            <strong>{customer}</strong><br>
            <span>{escape(state.case_id)} · synthetic support case</span>
          </div>
          <span class="status-pill status-{status}">{escape(_status_label(state.status))}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_metrics(state: CaseState) -> None:
    metrics = st.columns(4)
    metrics[0].metric("Demo severity", _human(state.severity), help="Generic impact model; not an Alloy severity rubric.")
    metrics[1].metric("Issue areas", _category_label(state))
    metrics[2].metric("Confirmed evidence", len(state.evidence))
    metrics[3].metric("Agent steps", f"{state.step_count} / {state.max_steps}")


def _render_questions(state: CaseState, provider: str) -> None:
    st.markdown(
        """
        <div class="outcome-card outcome-awaiting">
          <strong>Discovery required — the agent stopped safely</strong>
          The report does not yet contain enough evidence to distinguish the likely failure boundaries.
          These are the smallest useful follow-up questions, not a generic question dump.
        </div>
        """,
        unsafe_allow_html=True,
    )
    for index, question in enumerate(state.clarification_questions, start=1):
        st.markdown(
            f"""
            <div class="question-card">
              <span class="question-number">{index}</span>
              <span>{escape(question)}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
    active_scenario = _scenario(st.session_state.get("active_scenario"))
    sample_reply = None
    if active_scenario and len(state.customer_messages) == 1 and active_scenario.followups:
        sample_reply = active_scenario.followups[0]
    if sample_reply:
        st.caption("For a smooth walkthrough, continue with the bundled synthetic customer reply.")
        if st.button(
            "▶ Add sample customer evidence and continue",
            key="continue_sample_evidence",
            type="primary",
            width="stretch",
        ):
            _resume_case(state, sample_reply, provider=provider)
        with st.expander("Preview the sample reply"):
            st.write(sample_reply)
    with st.form("manual_reply_form"):
        reply = st.text_area(
            "Or enter a customer reply",
            placeholder="Include a timestamp, environment, and non-secret request or event identifier…",
            height=110,
            key="manual_reply",
        )
        resume = st.form_submit_button("Add evidence and resume")
    if resume:
        _resume_case(state, reply, provider=provider)


def _render_outcome(state: CaseState) -> None:
    if state.status == CaseStatus.RESOLVED:
        st.markdown(
            f"""
            <div class="outcome-card outcome-resolved">
              <strong>✓ Evidence-supported standard resolution</strong>
              {escape(state.resolution or 'The case was resolved.')}
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.caption("Customer-facing draft · human review required before sending")
    elif state.status == CaseStatus.ESCALATED:
        reason = state.escalation.escalation_reason if state.escalation else state.stop_reason
        st.markdown(
            f"""
            <div class="outcome-card outcome-escalated">
              <strong>↑ Investigation-ready engineering handoff</strong>
              {escape(reason or 'The evidence crossed the escalation threshold.')}
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.caption("Root cause remains explicitly unconfirmed · human review required before escalation")


def _signal(label: str, value: str) -> None:
    st.markdown(
        f"""
        <div class="signal-row">
          <div class="signal-label">{escape(label)}</div>
          <div class="signal-value">{escape(value)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_case_desk(state: CaseState, provider: str) -> None:
    left, right = st.columns([1.38, 0.82], gap="large")
    with left:
        st.markdown("#### Customer signal")
        with st.container(border=True):
            st.caption(f"FIRST CONTACT · {state.customer or 'CUSTOMER NOT SUPPLIED'}")
            st.markdown(f"> {state.original_message}")
            if len(state.customer_messages) > 1:
                st.caption("LATEST CUSTOMER EVIDENCE")
                st.write(state.customer_messages[-1])
        st.markdown("#### Current support response")
        if state.status == CaseStatus.AWAITING_CUSTOMER:
            _render_questions(state, provider)
        else:
            _render_outcome(state)
    with right:
        st.markdown("#### Discovery snapshot")
        with st.container(border=True):
            _signal("Customer impact", state.customer_impact or "Not yet established")
            _signal("Request IDs", _joined(state.request_ids))
            _signal("Webhook event IDs", _joined(state.webhook_ids))
            _signal("Non-secret credential labels", _joined(state.api_key_ids))
            _signal("Incident timestamps", _joined(state.timestamps))
            _signal("HTTP signals", _joined(state.http_codes))
            _signal("Endpoints", _joined(state.endpoints))
        st.markdown("#### Triage judgement")
        with st.container(border=True):
            _signal("Case status", _status_label(state.status))
            _signal("Current boundaries", _category_label(state))
            _signal("Next action", state.next_action or "Case has reached an explicit stop")
            _signal("Known gaps", _joined(state.missing_information, empty="No recorded gaps"))
        st.markdown(
            """
            <div class="safety-line">
              Severity is labelled <strong>demo severity</strong>. The app does not claim
              knowledge of any employer’s internal priority or SLA rubric.
            </div>
            """,
            unsafe_allow_html=True,
        )


def _render_investigation(state: CaseState) -> None:
    decisions = sum(event.event_type == EventType.DECISION for event in state.audit_trail)
    calls = sum(event.event_type == EventType.TOOL_CALL for event in state.audit_trail)
    guardrails = sum(event.event_type == EventType.GUARDRAIL for event in state.audit_trail)
    summary = st.columns(3)
    summary[0].metric("Decisions", decisions)
    summary[1].metric("Diagnostic calls", calls)
    summary[2].metric("Guardrail interventions", guardrails)
    st.caption(
        "This completed, append-only trace shows each decision and observation. Incident times come from the customer or tools; processing times are recorded separately."
    )
    show_state_updates = st.toggle(
        "Show low-level state updates",
        value=False,
        key=f"show_state_updates_{state.case_id}",
    )
    events = [
        event
        for event in state.audit_trail
        if show_state_updates or event.event_type != EventType.STATE_UPDATE
    ]
    trace_view = st.radio(
        "Trace view",
        ("Full completed trace", "Replay one recorded event"),
        horizontal=True,
        key=f"trace_view_{state.case_id}",
        help="Replay is a view over the completed deterministic audit trail; it is not fake live streaming.",
    )
    visible_events = events
    if trace_view == "Replay one recorded event" and events:
        position = st.slider(
            "Replay position",
            min_value=1,
            max_value=len(events),
            value=1,
            key=f"trace_replay_position_{state.case_id}",
        )
        st.progress(position / len(events))
        st.caption(
            f"Recorded event {position} of {len(events)} · use the slider to walk through the completed investigation."
        )
        visible_events = [events[position - 1]]
    for event in visible_events:
        label, icon = EVENT_PRESENTATION[event.event_type]
        tool_label = TOOL_LABELS.get(event.tool_name or "", event.tool_name or "")
        with st.container(border=True):
            heading, timestamp = st.columns([0.76, 0.24])
            with heading:
                suffix = f" · {tool_label}" if tool_label else ""
                st.markdown(
                    f"<span class='timeline-heading'>{icon} Step {event.step} · {label}{escape(suffix)}</span>",
                    unsafe_allow_html=True,
                )
            with timestamp:
                st.markdown(
                    f"<div class='timeline-meta' style='text-align:right'>{event.processing_timestamp.strftime('%H:%M:%S UTC')}</div>",
                    unsafe_allow_html=True,
                )
            if event.message:
                st.write(event.message)
            if event.arguments:
                st.caption("Validated arguments")
                st.code(json.dumps(event.arguments, indent=2, sort_keys=True), language="json")
            if event.result:
                with st.expander("Structured observation"):
                    st.json(event.result)


def _render_evidence(state: CaseState) -> None:
    facts, hypotheses = st.columns(2, gap="large")
    with facts:
        st.markdown("#### Confirmed facts")
        st.caption("Observations returned by a named diagnostic source.")
        if not state.evidence:
            st.info("No diagnostic evidence has been collected yet.")
        for index, item in enumerate(state.evidence, start=1):
            with st.container(border=True):
                st.markdown(f"**✓ Fact {index}**")
                st.write(item.fact)
                st.caption(f"Source: {item.source} · Observed: {item.observed_at or 'reference fact'}")
                if item.identifiers:
                    chips = "".join(
                        f'<span class="evidence-chip">{escape(str(key))}: {escape(str(value))}</span>'
                        for key, value in item.identifiers.items()
                    )
                    st.markdown(chips, unsafe_allow_html=True)
    with hypotheses:
        st.markdown("#### Hypotheses — not facts")
        st.caption("Possible explanations stay visibly unconfirmed unless evidence establishes them.")
        if not state.hypotheses:
            st.info("No hypotheses have been recorded.")
        for index, item in enumerate(state.hypotheses, start=1):
            with st.container(border=True):
                st.markdown(f"**◇ Hypothesis {index} · {item.status.upper()}**")
                st.write(item.statement)
                st.progress(int(item.confidence * 100))
                st.caption(f"Decision confidence: {item.confidence:.0%} · This is not proof of root cause.")
                if item.basis:
                    st.caption("Basis: " + "; ".join(item.basis))
    st.markdown("#### HTTP boundary map")
    st.caption("The same status code can mean different things at the API layer and at a customer webhook receiver.")
    if state.http_status_observations:
        rows = [
            {
                "HTTP": item.code,
                "Layer": _human(item.context),
                "Identifier": item.identifier or "—",
                "Source": item.source,
            }
            for item in state.http_status_observations
        ]
        st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.info("No HTTP observations have been scoped yet.")
    st.markdown("#### Safety and provenance")
    interventions = [event for event in state.audit_trail if event.event_type == EventType.GUARDRAIL]
    safety_columns = st.columns(3)
    safety_points = (
        ("Identifier provenance", "A diagnostic cannot use an ID that the customer or a prior tool did not supply."),
        ("Evidence threshold", "A successful diagnostic observation is required before the agent may resolve a case."),
        ("Bounded execution", f"The outer Python loop enforces a hard limit of {state.max_steps} decisions."),
    )
    for column, (title, copy) in zip(safety_columns, safety_points, strict=True):
        with column:
            with st.container(border=True):
                st.markdown(f"**🛡 {title}**")
                st.caption(copy)
    if interventions:
        with st.expander(f"Guardrail audit events ({len(interventions)})"):
            for event in interventions:
                st.write(f"Step {event.step}: {event.message}")


def _render_escalation(state: CaseState) -> None:
    escalation = state.escalation
    if escalation is None:
        if state.status == CaseStatus.RESOLVED:
            st.success("This standard case was resolved from the inspected evidence, so no engineering handoff was created.")
        else:
            st.info("A handoff will be built only if the evidence reaches an escalation condition.")
        return
    st.markdown(
        """
        <div class="outcome-card outcome-escalated">
          <strong>Engineering packet ready for human review</strong>
          The next owner receives the customer impact, correlation data, completed checks,
          current uncertainty, and the exact reason this case could not be closed safely at triage.
        </div>
        """,
        unsafe_allow_html=True,
    )
    headline = st.columns(3)
    headline[0].metric("Severity", _human(escalation.severity))
    headline[1].metric("Request IDs", len(escalation.request_ids))
    headline[2].metric("Webhook IDs", len(escalation.webhook_ids))
    left, right = st.columns(2, gap="large")
    with left:
        with st.container(border=True):
            st.markdown("**Why this crossed the triage boundary**")
            st.write(escalation.escalation_reason)
            st.caption("CUSTOMER IMPACT")
            st.write(escalation.customer_impact)
            st.caption("LIKELY ROOT CAUSE")
            st.warning(escalation.likely_root_cause)
        with st.container(border=True):
            st.markdown("**Correlation packet**")
            _signal("Request IDs", _joined(escalation.request_ids))
            _signal("Webhook IDs", _joined(escalation.webhook_ids))
            _signal("HTTP status codes", _joined(escalation.http_status_codes))
            _signal("Incident timestamps", _joined(escalation.timestamps))
    with right:
        with st.container(border=True):
            st.markdown("**Troubleshooting already performed**")
            for item in escalation.troubleshooting_performed:
                st.markdown(f"- {item}")
        with st.container(border=True):
            st.markdown("**Outstanding questions for the next owner**")
            if escalation.outstanding_questions:
                for item in escalation.outstanding_questions:
                    st.markdown(f"- {item}")
            else:
                st.write("No additional questions were generated.")
    payload = escalation.model_dump(mode="json")
    st.download_button(
        "Download structured handoff (.json)",
        data=json.dumps(payload, indent=2),
        file_name=f"{state.case_id}-engineering-handoff.json",
        mime="application/json",
        width="stretch",
    )
    with st.expander("Full diagnostic detail retained in the handoff"):
        st.json(escalation.relevant_details)


def _render_design(state: CaseState) -> None:
    st.divider()
    st.markdown('<div class="section-kicker">Role alignment</div>', unsafe_allow_html=True)
    st.subheader("How the project maps to Triage & Discovery")
    role_columns = st.columns(4)
    cards = (
        ("1 · First useful response", "Assesses the signal and asks only for evidence that changes the investigation path."),
        ("2 · Technical isolation", "Separates API, credential, platform, delivery and receiver boundaries using typed diagnostics."),
        ("3 · Support judgement", "Resolves standard cases only with evidence; escalates novel or high-impact uncertainty."),
        ("4 · Complete handoff", "Preserves impact, identifiers, checks, uncertainty and open questions for the next owner."),
    )
    for column, (title, copy) in zip(role_columns, cards, strict=True):
        with column:
            with st.container(border=True):
                st.markdown(f'<div class="role-card"><strong>{title}</strong><p>{copy}</p></div>', unsafe_allow_html=True)
    st.markdown("#### The actual agent loop")
    st.markdown(
        """
        <div class="architecture">
          <div class="arch-node">Customer report<small>Untrusted case input</small></div>
          <div class="arch-arrow">→</div>
          <div class="arch-node">Typed decision<small>Ask, diagnose, resolve, escalate</small></div>
          <div class="arch-arrow">→</div>
          <div class="arch-node">Validated tool<small>Allow-listed diagnostic</small></div>
          <div class="arch-arrow">→</div>
          <div class="arch-node">Evidence + state<small>Observe, update, repeat or stop</small></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.info(
        "The decision adapter is a policy layer, not a source of operational truth. "
        "Python validates every action; tools provide observations; the outer loop owns the step budget and stop conditions."
    )
    with st.expander("Technical detail · complete structured CaseState"):
        st.json(state.model_dump(mode="json"))


def _render_case(state: CaseState, provider: str) -> None:
    _render_case_strip(state)
    _render_metrics(state)
    case_desk, investigation, evidence, handoff = st.tabs(
        ("Case desk", "Investigation trail", "Evidence & judgement", "Handoff & design")
    )
    with case_desk:
        _render_case_desk(state, provider)
    with investigation:
        _render_investigation(state)
    with evidence:
        _render_evidence(state)
    with handoff:
        _render_escalation(state)
        _render_design(state)


def main() -> None:
    _apply_styles()
    provider, max_steps = _render_sidebar()
    _render_hero()
    state = st.session_state.get("case_state")
    _render_workflow(state)
    if state is None:
        _render_landing(provider, max_steps)
    else:
        active_provider = st.session_state.get("case_provider", provider)
        _render_scenario_switcher(state, active_provider)
        _render_case(state, active_provider)


if __name__ == "__main__":
    main()
