"""수정 루프·병합 테스트 — LLM 없이 루프와 심판의 기계 동작만 검증한다."""

from pipeline.loop import MAX_ATTEMPTS, merge_reports, run_pipeline, run_pipeline_events
from schemas.llm import LlmCall
from schemas.meal import DAYS, Meal, MealPlan, PlanRequest
from schemas.validation import PlanAudit, ValidationReport, Violation

LLM_VIOLATION = Violation(
    type="제약_위반",
    day="화",
    food_code="D101-004310000-0001",
    evidence="순대국밥 나트륨 470mg/100g × 900g = 4,230mg > 800mg",
    severity="high",
    suggestion="국물이 적은 밥류(비빔밥 등)로 교체",
)
CODE_VIOLATION = Violation(
    type="제약_위반",
    day="화",
    food_code="D101-004310000-0001",
    evidence="국밥_순대국밥 나트륨 470mg/100g × 900g = 4,230mg > 한도 800mg",
    severity="high",
    suggestion="나트륨이 낮은 음식(국물이 적은 밥류·구이류 등)으로 교체",
)


def fake_meal_plan() -> MealPlan:
    return MealPlan(
        meals=[
            Meal(
                day=day,
                food_code="D101-001000000-0001",
                food_name="비빔밥_전주비빔밥",
                serving_g=480,
                calories_kcal=614.4,
                protein_g=26.9,
                sodium_mg=720.0,
                reason="테스트",
            )
            for day in DAYS
        ]
    )


def wire(monkeypatch, llm_reports: list[ValidationReport], code_rounds: list[list[Violation]]) -> dict:
    """계획자·검증자·크로스체크를 전부 가짜로 갈아끼운다 — 루프만 남긴다."""
    calls = {"plan": 0, "revision_violations": []}

    def fake_plan(req, candidates, violations=None, previous=None, recorder=None):
        calls["plan"] += 1
        if violations:
            calls["revision_violations"].append(violations)
        if recorder is not None:  # 실제 client처럼 호출 기록을 남긴다
            recorder.append(
                LlmCall(role="planner", model="test/planner", prompt=[{"role": "user", "content": "p"}], raw_output="{}")
            )
        return fake_meal_plan()

    stub_audit = PlanAudit(
        constraints=PlanRequest(),
        meals=[],
        days_complete=True,
        rep_counts={},
        repetition_ok=True,
        weekly_sodium_mg=0.0,
    )
    llm_iter = iter(llm_reports)
    code_iter = iter(code_rounds)
    monkeypatch.setattr("pipeline.loop.plan_meals", fake_plan)
    monkeypatch.setattr("pipeline.loop.validate_plan", lambda req, plan, recorder=None: next(llm_iter))
    monkeypatch.setattr("pipeline.loop.crosscheck_plan", lambda req, plan, foods: next(code_iter))
    monkeypatch.setattr("pipeline.loop.audit_plan", lambda req, plan, foods: stub_audit)
    monkeypatch.setattr("pipeline.loop.load_foods", lambda: {})
    monkeypatch.setattr("pipeline.loop.select_candidates", lambda req, foods: [])
    return calls


def test_loop_revises_until_pass(monkeypatch):
    """1차 미통과 → 위반 회신 → 2차 통과. 증거는 attempts와 history에 남는다."""
    calls = wire(
        monkeypatch,
        [
            ValidationReport(passed=False, violations=[LLM_VIOLATION]),
            ValidationReport(passed=True, violations=[]),
        ],
        [[CODE_VIOLATION], []],
    )
    res = run_pipeline(PlanRequest())
    assert res.attempts == 2
    assert res.report.passed is True
    assert res.history[0].passed is False
    assert calls["plan"] == 2
    assert calls["revision_violations"] == [[LLM_VIOLATION]]  # 위반이 계획자에 되돌아갔다
    assert [call.attempt for call in res.llm_calls] == [1, 2]  # 호출 기록에 시도 번호가 새겨진다


def test_loop_gives_up_honestly_after_max_attempts(monkeypatch):
    """상한 3회 — 통과 못 하면 마지막 안과 위반 목록을 함께 반환한다 (정직한 실패)."""
    calls = wire(
        monkeypatch,
        [ValidationReport(passed=False, violations=[LLM_VIOLATION])] * MAX_ATTEMPTS,
        [[CODE_VIOLATION]] * MAX_ATTEMPTS,
    )
    res = run_pipeline(PlanRequest())
    assert res.attempts == MAX_ATTEMPTS
    assert res.report.passed is False
    assert res.report.violations == [LLM_VIOLATION]
    assert calls["plan"] == MAX_ATTEMPTS


def test_pipeline_events_narrate_the_loop(monkeypatch):
    """진행 이벤트가 루프의 실제 순서를 서술한다 — 마지막은 항상 결과."""
    wire(
        monkeypatch,
        [
            ValidationReport(passed=False, violations=[LLM_VIOLATION]),
            ValidationReport(passed=True, violations=[]),
        ],
        [[CODE_VIOLATION], []],
    )
    stages = [event.get("stage", event["event"]) for event in run_pipeline_events(PlanRequest())]
    assert stages == [
        "candidates",
        "planner", "validator", "violations",   # 시도 1: 위반 → 회신
        "planner", "validator", "passed",       # 시도 2: 통과
        "result",
    ]


def test_merge_keeps_validator_wording_when_code_confirms():
    """판정은 코드, 문장은 검증자 — 같은 위반이면 검증자의 해석·제안이 남는다."""
    merged = merge_reports(ValidationReport(passed=False, violations=[LLM_VIOLATION]), [CODE_VIOLATION])
    assert merged.passed is False
    assert merged.violations == [LLM_VIOLATION]


def test_merge_drops_unconfirmed_arithmetic_claims():
    """검증자가 "한도 내"까지 위반으로 보고하는 노이즈는 재계산 불일치로 폐기된다."""
    noise = LLM_VIOLATION.model_copy(update={"day": "수", "evidence": "765mg < 800mg (한도 내)"})
    merged = merge_reports(ValidationReport(passed=False, violations=[noise]), [])
    assert merged.passed is True
    assert merged.violations == []


def test_merge_appends_code_only_violations():
    """검증자가 놓쳐도 코드가 잡으면 위반이다 — 산수는 코드가 맞다."""
    merged = merge_reports(ValidationReport(passed=True, violations=[]), [CODE_VIOLATION])
    assert merged.passed is False
    assert merged.violations == [CODE_VIOLATION]
