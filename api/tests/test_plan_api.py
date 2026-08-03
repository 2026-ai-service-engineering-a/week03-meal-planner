"""API 테스트 — LLM 키 없이도 도는 안전망 (LLM 호출은 monkeypatch로 끊는다)."""

from fastapi.testclient import TestClient

from app.main import app
from pipeline.foods import load_foods, select_candidates
from schemas.meal import DAYS, Meal, MealPlan, PlanRequest

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


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_wrong_type_is_rejected_before_llm():
    """관문 ②: 틀린 입력은 LLM 요금이 나가기 전에 Pydantic이 공짜로 막는다."""
    res = client.post("/plan", json={"kcal_max": "많이"})
    assert res.status_code == 422


def test_plan_returns_seven_meals(monkeypatch):
    monkeypatch.setattr("app.main.plan_meals", lambda req, candidates: fake_meal_plan())
    res = client.post("/plan", json={})
    assert res.status_code == 200
    body = res.json()
    assert len(body["meals"]) == 7
    assert {meal["day"] for meal in body["meals"]} == set(DAYS)


def test_select_candidates_keeps_sodium_traps():
    """나트륨으로 후보를 거르지 않는다 — 수정 루프가 연출 없이 돌게 하는 설계."""
    foods = load_foods()
    picks = select_candidates(PlanRequest(), foods)
    names = [food["food_name"] for food in picks]
    assert "국밥_순대국밥" in names  # 나트륨 폭탄도 후보에 남는다
    assert "김치_배추김치" not in names  # 반찬 한 접시는 저녁 한 끼가 아니다
