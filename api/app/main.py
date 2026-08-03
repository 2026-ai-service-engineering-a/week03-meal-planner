"""식단 플래너 API — 2회전: 계획 → 검증 → 크로스체크 → 수정 루프 (지휘는 전부 코드).

LLM 호출 지점은 정확히 두 곳(계획자·검증자)뿐이고, 둘은 다른 회사 모델이다.
입구의 타입 보증(Pydantic·PlanRequest)과 출구의 타입 보증(instructor)이 만나는 곳.
헤드리스 구조: 이 API는 UI를 모른다. curl·Swagger(/docs)로 불러도 똑같이 동작한다.
"""

from fastapi import FastAPI

from pipeline.loop import run_pipeline
from schemas.meal import PlanRequest
from schemas.validation import PlanResponse

app = FastAPI(title="한 주 밥상 — 식단 플래너 API", version="1.0.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/plan")
def plan(req: PlanRequest) -> PlanResponse:
    """최종 식단 + 검증 리포트 + 시도 횟수 + 시도별 위반 이력 — 루프의 증거가 응답에 남는다."""
    return run_pipeline(req)
