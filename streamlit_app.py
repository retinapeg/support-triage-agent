"""Interactive Technical Support Engineer training console.

The human trainee owns the conversation and decisions. Cases and diagnostics
remain synthetic; optional OpenAI mode generates fresh customer language and
reactions without changing the fixture-backed operational truth.
"""

from __future__ import annotations

import json
import os
import random
import time
from html import escape

import streamlit as st

from simulation import (
    CASE_TEMPLATES,
    ConversationRole,
    MockCustomerSimulator,
    OpenAICustomerSimulator,
    SimulationCase,
    TrainingPhase,
    TrainingSession,
    apply_choice,
    build_incoming_queue,
    choices_for,
    generate_live_case,
    score_band,
    submit_free_text,
)


st.set_page_config(
    page_title="Support Shift · Live Triage Simulator",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

PHASE_LABELS = {
    TrainingPhase.DISCOVERY: "1 · Discover",
    TrainingPhase.DIAGNOSIS: "2 · Diagnose",
    TrainingPhase.RESPONSE: "3 · Resolve / escalate",
    TrainingPhase.COMPLETE: "Case complete",
}

PHASE_HELP = {
    TrainingPhase.DISCOVERY: "Get the smallest safe set of facts that separates likely causes.",
    TrainingPhase.DIAGNOSIS: "Use a supplied identifier to inspect the correct technical boundary.",
    TrainingPhase.RESPONSE: "Explain confirmed evidence, scope and the owned next action.",
    TrainingPhase.COMPLETE: "Review the result or take the next incident.",
}

PRIORITY_COLOURS = {"P1": "#ff5a78", "P2": "#ffb14a", "P3": "#55c2a3"}


def _apply_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --bg:#07101f; --panel:#0d192c; --line:rgba(166,190,229,.16);
            --ink:#eff5ff; --muted:#95a8c8; --cyan:#47d7ff;
            --green:#43d7a0; --amber:#ffbf69; color-scheme:dark;
        }
        .stApp {
            background:
              radial-gradient(circle at 86% -12%,rgba(79,110,255,.20),transparent 30rem),
              radial-gradient(circle at 5% 5%,rgba(36,204,229,.12),transparent 25rem),
              var(--bg);
            color:var(--ink);
        }
        .block-container {max-width:1440px;padding-top:1.25rem;padding-bottom:4rem}
        [data-testid="stMain"] *,[data-testid="stMain"] p,[data-testid="stMain"] label,
        [data-testid="stMain"] h1,[data-testid="stMain"] h2,[data-testid="stMain"] h3,
        [data-testid="stMain"] h4 {color:var(--ink)}
        [data-testid="stMain"] [data-testid="stCaptionContainer"] p {color:var(--muted)}
        [data-testid="stSidebar"] {
            background:linear-gradient(180deg,#0a1527,#09111f);
            border-right:1px solid var(--line)
        }
        [data-testid="stSidebar"] * {color:var(--ink)}
        [data-testid="stSidebar"] [data-baseweb="select"] *,
        [data-testid="stSidebar"] input {color:#17223b}
        [data-testid="stVerticalBlockBorderWrapper"] {
            background:linear-gradient(145deg,rgba(17,31,53,.96),rgba(11,24,43,.96));
            border-color:var(--line)!important;border-radius:16px
        }
        [data-testid="stMetric"] {
            background:rgba(15,30,52,.76);border:1px solid var(--line);
            border-radius:14px;padding:.75rem 1rem
        }
        [data-testid="stMetricLabel"] p {color:var(--muted)!important}
        [data-testid="stChatMessage"] {
            background:rgba(16,31,53,.86);border:1px solid var(--line);
            border-radius:16px;margin-bottom:.7rem
        }
        [data-testid="stChatInput"] {border-color:rgba(71,215,255,.35)}
        button[kind="primary"] {
            background:linear-gradient(120deg,#536dff,#2dbdd2)!important;
            border:0!important;color:white!important
        }
        button[kind="primary"] p {color:white!important}
        button[kind="secondary"] {
            background:rgba(20,38,65,.92)!important;
            border-color:rgba(151,180,229,.25)!important;color:var(--ink)!important
        }
        button[kind="secondary"] p {color:var(--ink)!important}
        button[kind="secondary"]:hover {border-color:var(--cyan)!important}
        .topbar {display:flex;justify-content:space-between;align-items:flex-end;gap:1rem;margin-bottom:1rem}
        .brand-kicker,.eyebrow {
            color:var(--cyan);font-size:.72rem;font-weight:850;
            letter-spacing:.15em;text-transform:uppercase
        }
        .topbar h1 {margin:.25rem 0 0;font-size:2.25rem;line-height:1.05}
        .live-chip {
            display:inline-flex;align-items:center;gap:.5rem;padding:.46rem .7rem;
            border-radius:999px;background:rgba(67,215,160,.10);
            border:1px solid rgba(67,215,160,.28);color:#a7f3d5;
            font-size:.76rem;font-weight:760
        }
        .live-dot {width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 0 5px rgba(67,215,160,.11)}
        .ticket-head {display:flex;justify-content:space-between;gap:.6rem;align-items:center}
        .priority {display:inline-block;padding:.25rem .48rem;border-radius:7px;font-size:.68rem;font-weight:900;letter-spacing:.08em}
        .ticket-title {font-weight:800;font-size:1rem;margin:.8rem 0 .35rem}
        .ticket-company {color:var(--muted);font-size:.78rem}
        .ticket-copy {color:#c5d2e8;font-size:.84rem;line-height:1.48;min-height:78px;margin:.55rem 0 .8rem}
        .ticket-meta {display:flex;gap:.42rem;flex-wrap:wrap;margin-bottom:.8rem}
        .meta-chip {
            padding:.24rem .48rem;border-radius:7px;background:rgba(118,145,190,.10);
            color:#aebfda;font-size:.7rem;border:1px solid rgba(118,145,190,.12)
        }
        .case-header {
            display:grid;grid-template-columns:1fr auto;gap:1rem;align-items:start;
            padding:1.25rem 1.35rem;border-radius:18px;
            background:linear-gradient(120deg,rgba(20,41,72,.95),rgba(20,54,79,.92));
            border:1px solid rgba(97,175,221,.22);margin-bottom:1rem
        }
        .case-header h2 {margin:.25rem 0 .4rem;font-size:1.5rem}
        .case-sub {color:#afbed6;font-size:.86rem}
        .stage-track {display:grid;grid-template-columns:repeat(3,1fr);gap:.45rem;margin:.8rem 0 1rem}
        .stage {
            padding:.65rem .72rem;border-radius:10px;border:1px solid var(--line);
            background:rgba(13,25,44,.75);color:var(--muted);font-size:.76rem;font-weight:720
        }
        .stage.done {background:rgba(67,215,160,.08);color:#96e8ca;border-color:rgba(67,215,160,.23)}
        .stage.active {background:rgba(82,111,255,.16);color:#d4dbff;border-color:rgba(108,131,255,.48)}
        .choice-intro {
            padding:.8rem .95rem;border-left:3px solid var(--cyan);
            background:rgba(71,215,255,.07);border-radius:0 10px 10px 0;
            margin:.3rem 0 1rem;color:#bdd7e6;font-size:.84rem
        }
        .feedback {
            padding:.85rem 1rem;margin:.7rem 0;border-radius:12px;
            background:rgba(255,191,105,.09);border:1px solid rgba(255,191,105,.22);
            color:#f5d7ad;font-size:.84rem;line-height:1.45
        }
        .feedback.good {background:rgba(67,215,160,.08);border-color:rgba(67,215,160,.24);color:#b0edd6}
        .tool-card {
            margin:.6rem 0;padding:.85rem 1rem;border-radius:12px;
            background:#091525;border:1px solid var(--line)
        }
        .tool-name {color:var(--cyan);font-family:ui-monospace,monospace;font-size:.8rem;font-weight:800}
        .tool-summary {color:#c4d1e4;margin-top:.35rem;font-size:.84rem}
        .score-hero {
            padding:1.4rem;border-radius:18px;text-align:center;
            background:linear-gradient(140deg,rgba(67,215,160,.12),rgba(83,109,255,.14));
            border:1px solid rgba(86,211,181,.25)
        }
        .score-number {font-size:3.5rem;line-height:1;font-weight:900;color:#ecfff8}
        .score-label {color:#9de7ce;font-weight:800;margin-top:.35rem}
        .safe-note {
            padding:.75rem .85rem;border-radius:10px;background:rgba(71,215,255,.06);
            border:1px solid rgba(71,215,255,.16);color:#a9c8da;font-size:.78rem;line-height:1.45
        }
        .empty-state {
            padding:2.8rem 1.2rem;text-align:center;border:1px dashed rgba(151,180,229,.28);
            border-radius:18px;color:var(--muted)
        }
        @media(max-width:850px) {
            .topbar{display:block}.case-header{grid-template-columns:1fr}.stage-track{grid-template-columns:1fr}
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _ensure_state() -> None:
    if "incoming_queue" not in st.session_state:
        queue = build_incoming_queue(count=4)
        st.session_state.incoming_queue = queue
        now = time.time()
        st.session_state.queue_arrivals = {
            case.case_id: now - index * 47 for index, case in enumerate(queue)
        }
    st.session_state.setdefault("completed_sessions", [])
    st.session_state.setdefault("active_training", None)
    st.session_state.setdefault("queue_arrivals", {})
    st.session_state.setdefault("last_arrival_at", time.time())


def _reset_shift() -> None:
    queue = build_incoming_queue(count=4)
    now = time.time()
    st.session_state.incoming_queue = queue
    st.session_state.queue_arrivals = {
        case.case_id: now - index * 42 for index, case in enumerate(queue)
    }
    st.session_state.completed_sessions = []
    st.session_state.active_training = None
    st.session_state.last_arrival_at = now


def _new_offline_incident() -> SimulationCase:
    existing = {case.template_id for case in st.session_state.incoming_queue}
    available = [case for case in CASE_TEMPLATES if case.template_id not in existing]
    base = random.choice(available or list(CASE_TEMPLATES))
    return base.model_copy(
        deep=True,
        update={"case_id": f"case-{base.template_id}-{random.randint(100000, 999999)}"},
    )


def _add_incident(case: SimulationCase) -> None:
    st.session_state.incoming_queue.insert(0, case)
    st.session_state.queue_arrivals[case.case_id] = time.time()
    st.session_state.last_arrival_at = time.time()


def _accept_case(case_id: str) -> None:
    case = next(
        (item for item in st.session_state.incoming_queue if item.case_id == case_id),
        None,
    )
    if case is None:
        st.error("That case is no longer in the queue.")
        return
    st.session_state.incoming_queue = [
        item for item in st.session_state.incoming_queue if item.case_id != case_id
    ]
    st.session_state.active_training = TrainingSession.start(case)
    st.session_state.active_case_started_epoch = time.time()


def _finish_case_to_queue() -> None:
    active = st.session_state.active_training
    if active is not None and active.phase == TrainingPhase.COMPLETE:
        completed_ids = {item.case.case_id for item in st.session_state.completed_sessions}
        if active.case.case_id not in completed_ids:
            st.session_state.completed_sessions.append(active)
    st.session_state.active_training = None


def _customer_engine(mode: str, api_key: str, model: str):
    if mode == "Live AI customer":
        return OpenAICustomerSimulator(api_key=api_key, model=model)
    return MockCustomerSimulator()


def _render_topbar(mode: str) -> None:
    engine_label = (
        "OPENAI CUSTOMER LIVE" if mode == "Live AI customer" else "SCENARIO ENGINE LIVE"
    )
    st.markdown(
        f"""
        <div class="topbar">
          <div>
            <div class="brand-kicker">Technical Support Engineer · Triage &amp; Discovery</div>
            <h1>Support Shift</h1>
          </div>
          <div class="live-chip"><span class="live-dot"></span>{engine_label}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_sidebar() -> tuple[str, str, str]:
    with st.sidebar:
        st.markdown("## ◈ Shift controls")
        st.caption("A hands-on support simulation—not a scripted walkthrough.")
        st.divider()
        mode = st.selectbox(
            "Customer engine",
            ("Scenario engine", "Live AI customer"),
            help="Live AI mode generates fresh wording and reacts to what you actually say.",
        )
        api_key = ""
        model = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
        if mode == "Live AI customer":
            api_key = st.text_input(
                "OpenAI API key",
                type="password",
                help="Held only in this running Streamlit session; never written to the project.",
                key="openai_api_key_session",
            )
            model = st.text_input(
                "Model",
                value=model,
                help="Change this only if your OpenAI project uses a different available model.",
            )
            if api_key:
                st.success("Live customer ready · key held in session only")
            else:
                st.warning("Enter your key here to enable generated incidents and customer replies.")
        else:
            st.success("Interactive offline mode ready")

        st.divider()
        left, right = st.columns(2)
        with left:
            if st.button("＋ Incoming", width="stretch", key="add_offline_incident"):
                _add_incident(_new_offline_incident())
                st.rerun()
        with right:
            if st.button("Reset shift", width="stretch", key="reset_shift"):
                _reset_shift()
                st.rerun()

        auto_arrivals = st.toggle(
            "Auto-arrivals",
            value=False,
            help="Adds another synthetic issue approximately every 20 seconds while this page is open.",
            key="auto_arrivals",
        )
        st.caption(
            "Demo timing · 20 seconds between arrivals"
            if auto_arrivals
            else "Auto-arrivals paused"
        )

        if mode == "Live AI customer":
            if st.button(
                "✦ Generate AI incident",
                type="primary",
                width="stretch",
                disabled=not bool(api_key),
                key="generate_ai_incident",
            ):
                base = random.choice(CASE_TEMPLATES)
                try:
                    with st.spinner("Generating a fresh fixture-grounded customer issue…"):
                        generated = generate_live_case(
                            base,
                            api_key=api_key,
                            model=model,
                        )
                    _add_incident(generated)
                    st.toast("A new AI-generated customer issue just arrived.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not generate the incident: {exc}")

        st.divider()
        st.metric("Open queue", len(st.session_state.incoming_queue))
        st.metric("Cases completed", len(st.session_state.completed_sessions))
        st.markdown(
            """
            <div class="safe-note">
              <strong>Safe demo boundary</strong><br>
              All case facts, companies and logs are synthetic. Live mode sends only this
              simulation plus your typed response to OpenAI. Never enter real customer data or secrets.
            </div>
            """,
            unsafe_allow_html=True,
        )
    return mode, api_key, model


@st.fragment(run_every=1)
def _arrival_ticker() -> None:
    if not st.session_state.get("auto_arrivals", False):
        return
    elapsed = time.time() - st.session_state.last_arrival_at
    remaining = max(0, int(20 - elapsed))
    st.caption(f"Next automatic issue in {remaining}s")
    if elapsed >= 20:
        _add_incident(_new_offline_incident())
        st.toast("New customer issue received")
        st.rerun(scope="app")


def _age_label(case_id: str) -> str:
    arrived = st.session_state.queue_arrivals.get(case_id, time.time())
    seconds = max(0, int(time.time() - arrived))
    return f"{seconds}s waiting" if seconds < 60 else f"{seconds // 60}m waiting"


def _priority_badge(priority: str) -> str:
    colour = PRIORITY_COLOURS.get(priority, "#9cb0cf")
    return (
        f'<span class="priority" style="color:{colour};background:{colour}1c;'
        f'border:1px solid {colour}44">{escape(priority)}</span>'
    )


def _render_queue(mode: str) -> None:
    st.markdown('<div class="eyebrow">Incoming workload</div>', unsafe_allow_html=True)
    left, right = st.columns([0.72, 0.28], vertical_alignment="bottom")
    with left:
        st.subheader("Live support queue")
        st.caption("Accept any case. Another issue can arrive while you are working.")
    with right:
        _arrival_ticker()

    queue: list[SimulationCase] = st.session_state.incoming_queue
    if not queue:
        st.markdown(
            '<div class="empty-state"><strong>Queue clear.</strong><br>Add an incoming issue or enable auto-arrivals.</div>',
            unsafe_allow_html=True,
        )
        return

    for row_start in range(0, len(queue), 3):
        row = queue[row_start : row_start + 3]
        columns = st.columns(3)
        for column, case in zip(columns, row, strict=False):
            with column:
                with st.container(border=True):
                    ai_chip = (
                        '<span class="meta-chip">✦ AI phrasing</span>'
                        if case.case_id.startswith("case-live-")
                        else ""
                    )
                    st.markdown(
                        f"""
                        <div class="ticket-head">
                          {_priority_badge(case.priority)}
                          <span class="ticket-company">{escape(_age_label(case.case_id))}</span>
                        </div>
                        <div class="ticket-title">{escape(case.title)}</div>
                        <div class="ticket-company">{escape(case.company)} · {escape(case.contact_name)}</div>
                        <div class="ticket-copy">{escape(case.initial_message)}</div>
                        <div class="ticket-meta">
                          <span class="meta-chip">{escape(case.category)}</span>
                          <span class="meta-chip">{escape(case.difficulty)}</span>
                          <span class="meta-chip">SLA {case.sla_minutes}m</span>
                          {ai_chip}
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    if st.button(
                        "Accept case →",
                        type="primary" if case.priority == "P1" else "secondary",
                        width="stretch",
                        key=f"accept_{case.case_id}",
                    ):
                        _accept_case(case.case_id)
                        st.rerun()

    if mode == "Scenario engine":
        st.caption(
            "Every queue item is interactive and fixture-backed. Switch to Live AI customer "
            "for generated wording and responses to your exact message."
        )


def _phase_index(phase: TrainingPhase) -> int:
    return {
        TrainingPhase.DISCOVERY: 0,
        TrainingPhase.DIAGNOSIS: 1,
        TrainingPhase.RESPONSE: 2,
        TrainingPhase.COMPLETE: 3,
    }[phase]


def _render_stage_track(session: TrainingSession) -> None:
    current = _phase_index(session.phase)
    labels = ("Discover", "Diagnose", "Resolve / escalate")
    cards = []
    for index, label in enumerate(labels):
        state = "done" if index < current else "active" if index == current else ""
        cards.append(f'<div class="stage {state}">{index + 1} · {label}</div>')
    st.markdown(
        f'<div class="stage-track">{"".join(cards)}</div>',
        unsafe_allow_html=True,
    )


def _sla_remaining(session: TrainingSession) -> tuple[str, bool]:
    started = st.session_state.get("active_case_started_epoch", time.time())
    remaining = session.case.sla_minutes * 60 - int(time.time() - started)
    breached = remaining <= 0
    minutes, seconds = divmod(abs(remaining), 60)
    label = f"+{minutes:02d}:{seconds:02d}" if breached else f"{minutes:02d}:{seconds:02d}"
    return label, breached


def _render_case_header(session: TrainingSession, mode: str) -> None:
    sla, breached = _sla_remaining(session)
    engine = (
        "AI-generated customer" if mode == "Live AI customer" else "Interactive scenario customer"
    )
    st.markdown(
        f"""
        <div class="case-header">
          <div>
            <div class="eyebrow">Active case · {escape(engine)}</div>
            <h2>{escape(session.case.title)}</h2>
            <div class="case-sub">{escape(session.case.company)} · {escape(session.case.contact_name)} · {escape(session.case.contact_role)}</div>
          </div>
          <div>{_priority_badge(session.case.priority)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    metrics = st.columns(4)
    metrics[0].metric("Current stage", PHASE_LABELS[session.phase])
    metrics[1].metric("Support score", f"{session.score}/100")
    metrics[2].metric("Customer mood", session.customer_mood)
    metrics[3].metric(
        "SLA",
        sla,
        "BREACHED" if breached else "remaining",
        delta_color="inverse",
    )
    _render_stage_track(session)


def _render_chat(session: TrainingSession) -> None:
    st.markdown("#### Customer conversation")
    st.caption(
        "Talk naturally. The customer responds to your wording and the coach assesses the support behaviour."
    )
    for message in session.conversation:
        if message.role == ConversationRole.SYSTEM:
            continue
        if message.role == ConversationRole.CUSTOMER:
            with st.chat_message("assistant", avatar="👤"):
                st.markdown(f"**{session.case.contact_name} · {session.case.company}**")
                st.write(message.content)
        else:
            with st.chat_message("user", avatar="🛟"):
                st.markdown("**You · Technical Support**")
                st.write(message.content)


def _render_context(session: TrainingSession) -> None:
    st.markdown("#### Case context")
    with st.container(border=True):
        st.markdown("**Category signal**")
        st.write(session.case.category)
        st.markdown("**Difficulty**")
        st.write(session.case.difficulty)
        st.markdown("**Customer tone**")
        st.write(session.case.customer_tone.title())
        st.markdown("**Current objective**")
        st.write(PHASE_HELP[session.phase])
    with st.expander(
        "Known customer-supplied evidence",
        expanded=session.phase != TrainingPhase.DISCOVERY,
    ):
        if session.phase == TrainingPhase.DISCOVERY:
            st.caption("No safe correlation identifiers collected yet.")
        else:
            st.json(session.case.confirmed_identifiers)


def _last_delta(session: TrainingSession) -> int | None:
    if not session.events:
        return None
    value = session.events[-1].detail.get("score_delta")
    return int(value) if isinstance(value, int) else None


def _render_feedback(session: TrainingSession) -> None:
    if not session.last_feedback:
        return
    delta = _last_delta(session)
    good = delta is not None and delta > 0
    delta_label = f"{delta:+d} points · " if delta is not None else ""
    css = "feedback good" if good else "feedback"
    st.markdown(
        f'<div class="{css}"><strong>{escape(delta_label)}Coach:</strong> '
        f"{escape(session.last_feedback)}</div>",
        unsafe_allow_html=True,
    )


def _render_choices(
    session: TrainingSession,
    mode: str,
    api_key: str,
    model: str,
) -> None:
    st.markdown("#### Choose your next action")
    st.markdown(
        f'<div class="choice-intro"><strong>{escape(PHASE_LABELS[session.phase])}</strong> · '
        f"{escape(PHASE_HELP[session.phase])}<br>"
        "Select the best of five, or write your own customer reply below.</div>",
        unsafe_allow_html=True,
    )
    _render_feedback(session)
    for index, choice in enumerate(choices_for(session), start=1):
        if st.button(
            f"{index}. {choice.label}",
            key=(
                f"choice_{session.case.case_id}_{session.phase.value}_"
                f"{session.attempts}_{choice.choice_id}"
            ),
            type="secondary",
            width="stretch",
        ):
            try:
                customer = _customer_engine(mode, api_key, model)
                message = (
                    "Customer is responding…"
                    if mode == "Live AI customer"
                    else "Applying the decision…"
                )
                with st.spinner(message):
                    apply_choice(session, choice.choice_id, customer)
                st.rerun()
            except Exception as exc:
                st.error(f"The customer interaction failed: {exc}")
    st.caption(
        "The correct answer moves position after each attempt. Poor choices have customer "
        "and score consequences; the case stays recoverable."
    )


def _render_evidence(session: TrainingSession) -> None:
    if not session.tool_results:
        st.info(
            "No diagnostic evidence yet. Complete discovery, then choose the "
            "boundary-specific diagnostic."
        )
        return
    for index, result in enumerate(reversed(session.tool_results), start=1):
        st.markdown(
            f"""
            <div class="tool-card">
              <div class="tool-name">{escape(result.tool_name)}</div>
              <div class="tool-summary">{escape(result.summary)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        with st.expander(f"Structured result {index}"):
            st.json(result.model_dump(mode="json"))


def _render_decision_log(session: TrainingSession) -> None:
    icons = {
        "case_arrived": "📥",
        "decision": "◇",
        "tool_result": "⚙",
        "free_text_evaluation": "💬",
        "live_customer_reaction": "✦",
    }
    for event in reversed(session.events):
        icon = icons.get(event.event_type, "•")
        st.markdown(
            f"{icon} **{event.event_type.replace('_', ' ').title()}** — {event.summary}"
        )
        if event.detail:
            st.caption(json.dumps(event.detail, default=str))


def _render_completion(session: TrainingSession) -> None:
    band, summary = score_band(session.score)
    st.markdown('<div class="eyebrow">Shift review</div>', unsafe_allow_html=True)
    st.subheader("Case complete")
    left, right = st.columns([0.36, 0.64])
    with left:
        st.markdown(
            f"""
            <div class="score-hero">
              <div class="score-number">{session.score}</div>
              <div class="score-label">{escape(band)} · {escape(session.outcome or "Complete")}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.caption(summary)
    with right:
        with st.container(border=True):
            st.markdown("#### What the strong answer demonstrated")
            st.write(session.case.coach_note)
            st.markdown("**Evidence-backed customer outcome**")
            st.write(session.case.resolution)
            st.markdown(
                f"**Attempts:** {session.attempts}  ·  "
                f"**Diagnostics:** {len(session.tool_results)}  ·  "
                f"**Final mood:** {session.customer_mood}"
            )

    left_button, retry_button, next_button = st.columns(3)
    with left_button:
        if st.button(
            "← Return to live queue",
            type="primary",
            width="stretch",
            key="complete_to_queue",
        ):
            _finish_case_to_queue()
            st.rerun()
    with retry_button:
        if st.button("Retry this issue", width="stretch", key="retry_case"):
            previous = session.case.model_copy(
                deep=True,
                update={
                    "case_id": (
                        f"case-retry-{session.case.template_id}-"
                        f"{random.randint(100000, 999999)}"
                    )
                },
            )
            st.session_state.active_training = TrainingSession.start(previous)
            st.session_state.active_case_started_epoch = time.time()
            st.rerun()
    with next_button:
        if st.button("Next issue →", width="stretch", key="complete_next_issue"):
            _finish_case_to_queue()
            if st.session_state.incoming_queue:
                _accept_case(st.session_state.incoming_queue[0].case_id)
            st.rerun()

    with st.expander("Full performance and evidence log"):
        st.json(session.model_dump(mode="json"))


def _render_active_case(
    session: TrainingSession,
    mode: str,
    api_key: str,
    model: str,
) -> None:
    back, _ = st.columns([0.18, 0.82])
    with back:
        if st.button("← Queue", key="back_to_queue", width="stretch"):
            st.session_state.incoming_queue.insert(0, session.case)
            st.session_state.queue_arrivals[session.case.case_id] = time.time()
            st.session_state.active_training = None
            st.rerun()

    _render_case_header(session, mode)
    if session.phase == TrainingPhase.COMPLETE:
        _render_completion(session)
        return

    chat_column, context_column = st.columns([0.66, 0.34])
    with chat_column:
        _render_chat(session)
    with context_column:
        _render_context(session)

    action_tab, evidence_tab, log_tab = st.tabs(
        [
            "Your next action",
            f"Diagnostic evidence · {len(session.tool_results)}",
            "Decision log",
        ]
    )
    with action_tab:
        _render_choices(session, mode, api_key, model)
    with evidence_tab:
        _render_evidence(session)
    with log_tab:
        _render_decision_log(session)

    prompt = {
        TrainingPhase.DISCOVERY: "Write your discovery response to the customer…",
        TrainingPhase.DIAGNOSIS: "Explain which diagnostic you will run and why…",
        TrainingPhase.RESPONSE: "Write the evidence-based resolution or escalation update…",
    }[session.phase]
    typed = st.chat_input(
        prompt,
        key=f"chat_{session.case.case_id}_{session.phase.value}",
    )
    if typed:
        try:
            customer = _customer_engine(mode, api_key, model)
            message = (
                "Customer is typing…"
                if mode == "Live AI customer"
                else "Customer is responding…"
            )
            with st.spinner(message):
                submit_free_text(session, typed, customer)
            st.rerun()
        except Exception as exc:
            st.error(f"The message could not be sent: {exc}")


def main() -> None:
    _apply_styles()
    _ensure_state()
    mode, api_key, model = _render_sidebar()
    _render_topbar(mode)
    session: TrainingSession | None = st.session_state.active_training
    if session is None:
        _render_queue(mode)
    else:
        _render_active_case(session, mode, api_key, model)


if __name__ == "__main__":
    main()
