"""Presenter-path smoke tests for the Streamlit interface."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from demo import SCENARIOS
from models import CaseStatus


def _new_app() -> AppTest:
    app_path = Path(__file__).resolve().parents[1] / "streamlit_app.py"
    app = AppTest.from_file(app_path, default_timeout=20).run()
    assert not app.exception
    return app


def _button_by_key(app: AppTest, key: str):
    return next(button for button in app.button if button.key == key)


def _button_by_label(app: AppTest, label: str):
    return next(button for button in app.button if button.label == label)


def _click_key(app: AppTest, key: str) -> None:
    _button_by_key(app, key).click()
    app.run()
    assert not app.exception


def test_initial_presenter_surface_is_safe_and_scenario_driven() -> None:
    app = _new_app()

    assert app.selectbox[0].label == "Decision mode"
    assert app.selectbox[0].value == "mock"
    assert app.slider[0].label == "Safety step limit"
    assert app.slider[0].value == 8
    assert {button.key for button in app.button} >= {
        "start_scenario_1",
        "start_scenario_2",
        "start_scenario_3",
    }
    rendered_copy = "\n".join(str(item.value) for item in app.markdown)
    assert "Synthetic portfolio demo" in rendered_copy
    assert "Triage &amp; Discovery" in rendered_copy


def test_guided_authentication_case_pauses_then_resolves_same_case() -> None:
    app = _new_app()

    _button_by_key(app, "start_scenario_1").click()
    app.run()

    assert not app.exception
    waiting_state = app.session_state["case_state"]
    case_id = waiting_state.case_id
    assert waiting_state.status == CaseStatus.AWAITING_CUSTOMER
    assert not waiting_state.evidence
    assert _button_by_label(app, "▶ Add sample customer evidence and continue")

    _click_key(app, "continue_sample_evidence")

    assert not app.exception
    resolved_state = app.session_state["case_state"]
    assert resolved_state.case_id == case_id
    assert resolved_state.status == CaseStatus.RESOLVED
    assert len(resolved_state.evidence) == 2
    metrics = {metric.label: metric.value for metric in app.metric}
    assert metrics["Issue areas"] == "Authentication + API"


def test_escalation_case_exposes_mixed_boundaries_and_downloadable_handoff() -> None:
    app = _new_app()

    _button_by_key(app, "start_scenario_3").click()
    app.run()

    assert not app.exception
    state = app.session_state["case_state"]
    assert state.status == CaseStatus.ESCALATED
    assert state.escalation is not None
    metrics = {metric.label: metric.value for metric in app.metric}
    assert metrics["Issue areas"] == "API + Webhook"
    assert any(
        item.label == "Download structured handoff (.json)"
        for item in app.get("download_button")
    )

    _click_key(app, "clear_case_to_gallery")
    assert not app.exception
    assert "case_state" not in app.session_state


def test_active_case_can_switch_directly_to_another_guided_scenario() -> None:
    app = _new_app()

    _click_key(app, "start_scenario_1")
    first_state = app.session_state["case_state"]
    first_case_id = first_state.case_id

    assert app.session_state["active_scenario"] == 1
    assert first_state.status == CaseStatus.AWAITING_CUSTOMER
    assert {button.key for button in app.button} >= {
        "switch_scenario_1",
        "switch_scenario_2",
        "switch_scenario_3",
        "scenario_previous",
        "scenario_rerun",
        "scenario_next",
    }

    _click_key(app, "switch_scenario_3")

    switched = app.session_state["case_state"]
    assert app.session_state["active_scenario"] == 3
    assert switched.case_id != first_case_id
    assert switched.customer == SCENARIOS[2].customer
    assert switched.original_message == SCENARIOS[2].issue
    assert switched.status == CaseStatus.ESCALATED
    assert switched.escalation is not None
    assert switched.resolution is None
    assert switched.max_steps == 8
    assert app.session_state["case_provider"] == "mock"


def test_previous_next_and_rerun_controls_create_fresh_cases() -> None:
    app = _new_app()

    _click_key(app, "start_scenario_2")
    scenario_two_id = app.session_state["case_state"].case_id
    assert not _button_by_key(app, "scenario_previous").disabled
    assert not _button_by_key(app, "scenario_next").disabled

    _click_key(app, "scenario_previous")
    scenario_one = app.session_state["case_state"]
    assert app.session_state["active_scenario"] == 1
    assert scenario_one.case_id != scenario_two_id
    assert scenario_one.original_message == SCENARIOS[0].issue
    assert scenario_one.status == CaseStatus.AWAITING_CUSTOMER
    assert _button_by_key(app, "scenario_previous").disabled

    _click_key(app, "continue_sample_evidence")
    resolved = app.session_state["case_state"]
    assert resolved.status == CaseStatus.RESOLVED
    assert len(resolved.evidence) == 2

    _click_key(app, "scenario_rerun")
    rerun = app.session_state["case_state"]
    assert app.session_state["active_scenario"] == 1
    assert rerun.case_id != resolved.case_id
    assert rerun.status == CaseStatus.AWAITING_CUSTOMER
    assert len(rerun.customer_messages) == 1
    assert not rerun.evidence
    assert rerun.resolution is None

    _click_key(app, "scenario_next")
    scenario_two = app.session_state["case_state"]
    assert app.session_state["active_scenario"] == 2
    assert scenario_two.original_message == SCENARIOS[1].issue
    assert scenario_two.status == CaseStatus.AWAITING_CUSTOMER


def test_switched_scenario_keeps_the_correct_interactive_reply_and_trace_replay() -> None:
    app = _new_app()

    _click_key(app, "start_scenario_1")
    abandoned_id = app.session_state["case_state"].case_id
    _click_key(app, "switch_scenario_2")

    waiting = app.session_state["case_state"]
    waiting_id = waiting.case_id
    assert waiting_id != abandoned_id
    assert waiting.status == CaseStatus.AWAITING_CUSTOMER

    _click_key(app, "continue_sample_evidence")
    resolved = app.session_state["case_state"]
    assert resolved.case_id == waiting_id
    assert resolved.status == CaseStatus.RESOLVED
    assert resolved.customer_messages[-1] == SCENARIOS[1].followups[0]

    trace_view = next(item for item in app.radio if item.label == "Trace view")
    trace_view.set_value("Replay one recorded event")
    app.run()
    assert not app.exception
    replay = next(item for item in app.slider if item.label == "Replay position")
    assert replay.min == 1
    assert replay.max > 1
