"""LLM 호출 기록 계약 — 관측 데이터도 스키마가 있다.

로그(stdout)에만 있던 입력·출력 전문을 응답에도 실어, 재현자가 UI에서
"모델이 실제로 본 것과 뱉은 것"을 열어볼 수 있게 한다. 개발 참고용이다.
"""

from pydantic import BaseModel, Field


class LlmCall(BaseModel):
    """LLM 호출 1건의 입력·출력 전문."""

    role: str  # planner | validator
    model: str = Field(description="실제 사용된 모델 — 폴백이 동작했다면 폴백 모델")
    label: str = ""  # "(revision)" 등 호출 맥락
    attempt: int | None = None  # 수정 루프의 시도 번호 (loop가 채운다)
    prompt: list[dict[str, str]]  # [{"role": "system"|"user", "content": 전문}]
    raw_output: str = Field(description="모델 원출력 — instructor 파싱 전의 그 텍스트")
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None  # 비용표에 없는 모델이면 None
    duration_s: float | None = None
