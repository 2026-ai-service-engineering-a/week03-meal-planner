"""식단 플래너 API — v0.1: 목 응답만 있는 뼈대.

UI·compose·데이터가 먼저 자리를 잡고, LLM 파이프라인은
1회전(feature/planner)·2회전(feature/validator-loop)에서 채워진다.
헤드리스 구조: 이 API는 UI를 모른다. curl·Swagger(/docs)로 불러도 똑같이 동작한다.
"""

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="한 주 밥상 — 식단 플래너 API", version="0.1.0")


class PlanRequest(BaseModel):
    """POST /plan 입력 — UI 폼과 1:1 대응.

    틀린 타입은 LLM에 가기도 전에 여기서 422로 죽는다. 첫 번째 관문.
    """

    kcal_min: int = 500
    kcal_max: int = 800
    sodium_limit_mg: int = 800  # 저녁 몫 나트륨 예산
    protein_min_g: int = 25
    request: str = ""  # 자유 요청사항 한 줄 — 비우면 조건만으로 생성. 상태는 이 필드가 전부


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/plan")
def plan(req: PlanRequest) -> dict:
    """v0.1 목 응답 — 계획자·검증자는 아직 없다."""
    return {
        "meals": [],
        "report": None,
        "attempts": 0,
        "history": [],
        "message": "아직 영양사가 출근 전입니다",
    }
