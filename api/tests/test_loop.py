"""수정 루프 테스트 — LLM 없이 루프의 기계 동작만 검증한다."""

from pipeline.loop import MAX_ATTEMPTS, run_pipeline
from schemas.meal import DAYS, Meal, MealPlan, PlanRequest
from schemas.validation import ValidationReport, Violation

VIOLATION = Violation(
    type="제약_위반",
    day="화",
    food_code="D101-004310000-0001",
    evidence="순대국밥 나트륨 470mg/100g × 900g = 4,230mg > 800mg",
    severity="high",
    suggestion="국물이 적은 밥류(비빔밥 등)로 교체",
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


def wire(monkeypatch, reports: list[ValidationReport]) -> dict:
    """계획자·검증자·크로스체크를 전부 가짜로 갈아끼운다 — 루프만 남긴다."""
    calls = {"plan": 0, "revision_violations": []}

    def fake_plan(req, candidates, violations=None):
        calls["plan"] += 1
        if violations:
            calls["revision_violations"].append(violations)
        return fake_meal_plan()

    report_iter = iter(reports)
    monkeypatch.setattr("pipeline.loop.plan_meals", fake_plan)
    monkeypatch.setattr("pipeline.loop.validate_plan", lambda req, plan: next(report_iter))
    monkeypatch.setattr("pipeline.loop.crosscheck_plan", lambda req, plan, foods: [])
    monkeypatch.setattr("pipeline.loop.load_foods", lambda: {})
    monkeypatch.setattr("pipeline.loop.select_candidates", lambda req, foods: [])
    return calls


def test_loop_revises_until_pass(monkeypatch):
    """1차 미통과 → 위반 회신 → 2차 통과. 증거는 attempts와 history에 남는다."""
    calls = wire(
        monkeypatch,
        [
            ValidationReport(passed=False, violations=[VIOLATION]),
            ValidationReport(passed=True, violations=[]),
        ],
    )
    res = run_pipeline(PlanRequest())
    assert res.attempts == 2
    assert res.report.passed is True
    assert res.history[0].passed is False
    assert calls["plan"] == 2
    assert calls["revision_violations"] == [[VIOLATION]]  # 위반이 계획자에 되돌아갔다


def test_loop_gives_up_honestly_after_max_attempts(monkeypatch):
    """상한 3회 — 통과 못 하면 마지막 안과 위반 목록을 함께 반환한다 (정직한 실패)."""
    calls = wire(
        monkeypatch,
        [ValidationReport(passed=False, violations=[VIOLATION])] * MAX_ATTEMPTS,
    )
    res = run_pipeline(PlanRequest())
    assert res.attempts == MAX_ATTEMPTS
    assert res.report.passed is False
    assert res.report.violations == [VIOLATION]
    assert calls["plan"] == MAX_ATTEMPTS
