from simulation import (
    CASE_BY_TEMPLATE,
    MockCustomerSimulator,
    TrainingPhase,
    TrainingSession,
    apply_choice,
    build_incoming_queue,
    choices_for,
    score_band,
    submit_free_text,
)


def _session(template_id: str = "auth-expired") -> TrainingSession:
    return TrainingSession.start(CASE_BY_TEMPLATE[template_id].model_copy(deep=True))


def test_incoming_queue_is_varied_and_uses_fresh_case_ids() -> None:
    first = build_incoming_queue(count=5, seed=42)
    second = build_incoming_queue(count=5, seed=42)

    assert [item.template_id for item in first] == [item.template_id for item in second]
    assert len({item.template_id for item in first}) == 5
    assert [item.case_id for item in first] != [item.case_id for item in second]


def test_every_active_stage_has_exactly_five_choices_and_one_best_answer() -> None:
    session = _session()
    for phase in (
        TrainingPhase.DISCOVERY,
        TrainingPhase.DIAGNOSIS,
        TrainingPhase.RESPONSE,
    ):
        session.phase = phase
        choices = choices_for(session)
        assert len(choices) == 5
        assert sum(choice.is_best for choice in choices) == 1
        assert len({choice.choice_id for choice in choices}) == 5


def test_best_choices_progress_through_a_complete_scored_case() -> None:
    session = _session("webhook-endpoint-5xx")

    for expected_phase in (
        TrainingPhase.DISCOVERY,
        TrainingPhase.DIAGNOSIS,
        TrainingPhase.RESPONSE,
    ):
        assert session.phase == expected_phase
        best = next(choice for choice in choices_for(session) if choice.is_best)
        apply_choice(session, best.choice_id)

    assert session.phase == TrainingPhase.COMPLETE
    assert session.score == 100
    assert session.outcome == "Customer given a safe resolution"
    assert session.tool_results[-1].tool_name == "inspect_webhook_delivery"
    assert session.tool_results[-1].success
    assert session.completed_at is not None


def test_wrong_choice_has_customer_consequence_without_advancing() -> None:
    session = _session()
    unsafe = next(choice for choice in choices_for(session) if "API key" in choice.label)

    apply_choice(session, unsafe.choice_id)

    assert session.phase == TrainingPhase.DISCOVERY
    assert session.score == 0
    assert session.customer_mood == "Alarmed"
    assert "cannot share credentials" in session.conversation[-1].content


def test_free_text_discovery_and_diagnosis_execute_the_expected_path() -> None:
    session = _session("rate-limit")
    simulator = MockCustomerSimulator()

    discovery = submit_free_text(
        session,
        "I understand the impact. Please share a request ID, status code, timestamp and timezone, production or sandbox environment, and affected scope.",
        simulator,
    )
    assert discovery.advance
    assert session.phase == TrainingPhase.DIAGNOSIS

    diagnosis = submit_free_text(
        session,
        "I will inspect the supplied request in the gateway trace before drawing a conclusion.",
        simulator,
    )
    assert diagnosis.advance
    assert session.phase == TrainingPhase.RESPONSE
    assert session.tool_results[-1].tool_name == "inspect_api_request"
    assert session.tool_results[-1].data["status_code"] == 429


def test_internal_failure_finishes_as_evidence_backed_escalation() -> None:
    session = _session("internal-500")
    for _ in range(3):
        best = next(choice for choice in choices_for(session) if choice.is_best)
        apply_choice(session, best.choice_id)

    assert session.phase == TrainingPhase.COMPLETE
    assert session.outcome == "Escalated with evidence"
    assert "rules_evaluation" in session.case.resolution


def test_score_band_is_clear_at_each_boundary() -> None:
    assert score_band(100)[0] == "Expert"
    assert score_band(90)[0] == "Expert"
    assert score_band(70)[0] == "Strong"
    assert score_band(45)[0] == "Developing"
    assert score_band(44)[0] == "Needs work"
