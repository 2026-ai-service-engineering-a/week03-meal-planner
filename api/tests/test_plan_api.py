"""API 테스트 — LLM 키 없이도 도는 안전망 (LLM 호출은 monkeypatch로 끊는다)."""

import json

from fastapi.testclient import TestClient

from app.main import app
from pipeline.foods import load_foods, select_candidates
from schemas.meal import DAYS, Meal, MealPlan, PlanRequest
from schemas.validation import AttemptRecord, PlanAudit, PlanResponse, ValidationReport

client = TestClient(app)


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
                reason="테스트 픽스처",
            )
            for day in DAYS
        ]
    )


def fake_audit() -> PlanAudit:
    return PlanAudit(
        constraints=PlanRequest(),
        meals=[],
        days_complete=True,
        rep_counts={},
        repetition_ok=True,
        weekly_sodium_mg=0.0,
    )


def fake_plan_response() -> PlanResponse:
    report = ValidationReport(passed=True, violations=[])
    return PlanResponse(
        meals=fake_meal_plan().meals,
        report=report,
        attempts=1,
        history=[AttemptRecord(attempt=1, passed=True, violations=[])],
        audit=fake_audit(),
    )


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_wrong_type_is_rejected_before_llm():
    """관문 ②: 틀린 입력은 LLM 요금이 나가기 전에 Pydantic이 공짜로 막는다."""
    res = client.post("/plan", json={"kcal_max": "많이"})
    assert res.status_code == 422


def test_plan_returns_seven_meals_with_report(monkeypatch):
    monkeypatch.setattr("app.main.run_pipeline", lambda req: fake_plan_response())
    res = client.post("/plan", json={})
    assert res.status_code == 200
    body = res.json()
    assert len(body["meals"]) == 7
    assert {meal["day"] for meal in body["meals"]} == set(DAYS)
    assert body["report"]["passed"] is True
    assert body["attempts"] == 1
    assert body["history"][0]["attempt"] == 1
    assert body["audit"]["days_complete"] is True  # 통과의 근거(감사)가 응답에 실린다


def test_plan_stream_emits_progress_then_result(monkeypatch):
    """SSE는 과정 + 결과 — 마지막 data 이벤트가 /plan과 같은 응답이다."""

    def fake_events(req):
        yield {"event": "progress", "stage": "planner", "detail": "계획자 호출", "max_attempts": 3}
        yield {"event": "result", "data": fake_plan_response()}

    monkeypatch.setattr("app.main.run_pipeline_events", fake_events)
    res = client.post("/plan/stream", json={})
    assert res.status_code == 200
    events = [json.loads(line[len("data: "):]) for line in res.text.splitlines() if line.startswith("data: ")]
    assert [e["event"] for e in events] == ["progress", "result"]
    assert len(events[-1]["data"]["meals"]) == 7


def test_select_candidates_keeps_sodium_traps():
    """나트륨으로 후보를 거르지 않는다 — 수정 루프가 연출 없이 돌게 하는 설계."""
    foods = load_foods()
    picks = select_candidates(PlanRequest(), foods)
    names = [food["food_name"] for food in picks]
    assert "국밥_순대국밥" in names  # 나트륨 폭탄도 후보에 남는다
    assert "김치_배추김치" not in names  # 반찬 한 접시는 저녁 한 끼가 아니다
