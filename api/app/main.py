"""식단 플래너 API — 1회전: /plan이 형식이 보증된 식단 초안을 반환한다 (검증은 아직 없음).

입구의 타입 보증(Pydantic·PlanRequest)과 출구의 타입 보증(instructor·MealPlan)이
만나는 곳. 데이터는 객체 → 텍스트 → JSON → 객체를 여행하고, 경계마다 보증자가 다르다.
헤드리스 구조: 이 API는 UI를 모른다. curl·Swagger(/docs)로 불러도 똑같이 동작한다.
"""

from fastapi import FastAPI

from pipeline.foods import load_foods, select_candidates
from pipeline.planner import plan_meals
from schemas.meal import MealPlan, PlanRequest

app = FastAPI(title="한 주 밥상 — 식단 플래너 API", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/plan")
def plan(req: PlanRequest) -> MealPlan:
    """저녁 7끼 식단 초안 — 계획자(PLANNER_MODEL) 호출은 정확히 한 곳."""
    candidates = select_candidates(req, load_foods())
    return plan_meals(req, candidates)
