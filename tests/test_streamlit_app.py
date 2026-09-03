"""Smoke tests for the interactive support-shift interface."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from simulation import TrainingPhase, choices_for


def _new_app() -> AppTest:
    app_path = Path(__file__).resolve().parents[1] / "streamlit_app.py"
    app = AppTest.from_file(app_path, default_timeout=20).run()
    assert not app.exception
    return app


def _button_by_key(app: AppTest, key: str):
    return next(button for button in app.button if button.key == key)


def _first_button_with_prefix(app: AppTest, prefix: str):
    return next(button for button in app.button if (button.key or "").startswith(prefix))


def _accept_first_case(app: AppTest) -> None:
    _first_button_with_prefix(app, "accept_").click()
    app.run()
    assert not app.exception


def _choose_best(app: AppTest) -> None:
    session = app.session_state["active_training"]
    best = next(choice for choice in choices_for(session) if choice.is_best)
    button = next(
        item
        for item in app.button
        if (item.key or "").startswith("choice_")
        and (item.key or "").endswith(best.choice_id)
    )
    button.click()
    app.run()
    assert not app.exception


def test_initial_surface_is_a_live_queue_not_a_scenario_gallery() -> None:
    app = _new_app()

    assert app.selectbox[0].label == "Customer engine"
    assert app.selectbox[0].value == "Scenario engine"
    assert len(app.session_state["incoming_queue"]) == 4
    assert len(
        [button for button in app.button if (button.key or "").startswith("accept_")]
    ) == 4
    rendered = "\n".join(str(item.value) for item in app.markdown)
    assert "Support Shift" in rendered
    assert "Live support queue" in "\n".join(item.value for item in app.subheader)
    assert "Technical Support Engineer" in rendered


def test_accepting_case_opens_customer_chat_and_five_actions() -> None:
    app = _new_app()
    initial_queue_size = len(app.session_state["incoming_queue"])

    _accept_first_case(app)

    session = app.session_state["active_training"]
    assert session.phase == TrainingPhase.DISCOVERY
    assert len(app.session_state["incoming_queue"]) == initial_queue_size - 1
    assert len(session.conversation) == 1
    choice_buttons = [
        button for button in app.button if (button.key or "").startswith("choice_")
    ]
    assert len(choice_buttons) == 5
    assert len(app.chat_input) == 1
    assert "Write your discovery response" in app.chat_input[0].placeholder


def test_best_answers_drive_case_through_diagnostic_to_scorecard() -> None:
    app = _new_app()
    _accept_first_case(app)

    _choose_best(app)
    session = app.session_state["active_training"]
    assert session.phase == TrainingPhase.DIAGNOSIS
    assert session.score == 25
    assert len(session.conversation) == 3

    _choose_best(app)
    session = app.session_state["active_training"]
    assert session.phase == TrainingPhase.RESPONSE
    assert session.score == 60
    assert len(session.tool_results) == 1
    assert session.tool_results[0].success

    _choose_best(app)
    session = app.session_state["active_training"]
    assert session.phase == TrainingPhase.COMPLETE
    assert session.score == 100
    assert session.outcome is not None
    assert _button_by_key(app, "complete_to_queue")
    rendered = "\n".join(str(item.value) for item in app.markdown)
    assert "score-number\">100" in rendered


def test_unsafe_answer_changes_customer_mood_without_advancing() -> None:
    app = _new_app()
    _accept_first_case(app)
    session = app.session_state["active_training"]
    unsafe = next(choice for choice in choices_for(session) if "API key" in choice.label)
    unsafe_button = next(
        item for item in app.button if (item.key or "").endswith(unsafe.choice_id)
    )

    unsafe_button.click()
    app.run()

    session = app.session_state["active_training"]
    assert not app.exception
    assert session.phase == TrainingPhase.DISCOVERY
    assert session.customer_mood == "Alarmed"
    assert "cannot share credentials" in session.conversation[-1].content
    assert len(
        [button for button in app.button if (button.key or "").startswith("choice_")]
    ) == 5


def test_live_ai_mode_exposes_session_only_key_input_and_generation_control() -> None:
    app = _new_app()

    app.selectbox[0].set_value("Live AI customer")
    app.run()

    assert not app.exception
    assert {item.label for item in app.text_input} >= {"OpenAI API key", "Model"}
    generate = _button_by_key(app, "generate_ai_incident")
    assert generate.disabled


def test_manual_incoming_control_adds_a_new_ticket() -> None:
    app = _new_app()
    before = len(app.session_state["incoming_queue"])

    _button_by_key(app, "add_offline_incident").click()
    app.run()

    assert not app.exception
    assert len(app.session_state["incoming_queue"]) == before + 1


def test_completed_case_is_recorded_when_returning_to_queue() -> None:
    app = _new_app()
    _accept_first_case(app)
    _choose_best(app)
    _choose_best(app)
    _choose_best(app)

    _button_by_key(app, "complete_to_queue").click()
    app.run()

    assert not app.exception
    assert app.session_state["active_training"] is None
    assert len(app.session_state["completed_sessions"]) == 1
    assert any((button.key or "").startswith("accept_") for button in app.button)
