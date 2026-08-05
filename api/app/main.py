"""식단 플래너 API — 계획 → 검증 → 크로스체크 → 수정 루프 (지휘는 전부 코드).

LLM 호출 지점은 정확히 두 곳(계획자·검증자)뿐이고, 둘은 다른 회사 모델이다.
입구의 타입 보증(Pydantic·PlanRequest)과 출구의 타입 보증(instructor)이 만나는 곳.
헤드리스 구조: 이 API는 UI를 모른다. curl·Swagger(/docs)로 불러도 똑같이 동작한다.
"""

import json
import os

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from pipeline.loop import run_pipeline, run_pipeline_events
from schemas.meal import PlanRequest
from schemas.validation import PlanResponse

app = FastAPI(title="한 주 밥상 — 식단 플래너 API", version="1.0.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/config")
def config() -> dict:
    """역할별 모델 구성 — env가 곧 조직도이고, UI 사이드바가 이걸 그대로 보여준다.

    키는 절대 내보내지 않는다. 모델 문자열은 비밀이 아니라 정책이다.
    """
    return {
        "planner_model": os.environ.get("PLANNER_MODEL", ""),
        "validator_model": os.environ.get("VALIDATOR_MODEL", ""),
        "validator_fallbacks": [
            model.strip()
            for model in os.environ.get("VALIDATOR_FALLBACKS", "").split(",")
            if model.strip()
        ],
    }


@app.post("/plan")
def plan(req: PlanRequest) -> PlanResponse:
    """최종 식단 + 검증 리포트 + 시도 이력 + 감사(통과의 근거) — 루프의 증거가 응답에 남는다."""
    return run_pipeline(req)


@app.post("/plan/stream")
def plan_stream(req: PlanRequest) -> StreamingResponse:
    """/plan과 같은 일, 다만 진행 이벤트를 SSE로 흘린다 (UI의 중간 과정 표시용).

    서버에 작업 상태 저장 없음 — 진행 상태는 이 HTTP 연결 안에만 산다.
    이벤트: {"event": "progress", "stage": ..., "detail": ...} × N, 마지막에
    {"event": "result", "data": PlanResponse}.
    """

    def sse():
        for event in run_pipeline_events(req):
            if event["event"] == "result":
                event = {"event": "result", "data": event["data"].model_dump()}
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
