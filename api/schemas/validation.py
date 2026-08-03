"""검증자 계약서 — 판정에 근거를 강제한다.

"나트륨 초과 같아요"가 아니라 "470mg/100g × 900g = 4,230mg > 800mg"을
내놓게 하는 것이 evidence 필드다. structured output이 판정의 품질을 끌어올린다.

검증자의 출력은 사람이 읽는 글이 아니라 다음 코드가 소비하는 데이터다:
passed 하나로 코드가 루프를 돌릴지 결정하고, violations의 evidence·suggestion은
계획자의 다음 프롬프트에 그대로 들어간다. 검증자 출력이 계획자 입력이 되는 배선.
"""

from typing import Literal

from pydantic import BaseModel, Field


class Violation(BaseModel):
    type: Literal["존재하지_않는_음식", "환산_오류", "제약_위반", "중복"]
    day: str
    food_code: str
    evidence: str = Field(description='판정 근거를 계산식으로 (예: "470mg/100g × 900g = 4,230mg > 800mg")')
    severity: Literal["high", "medium", "low"]
    suggestion: str = Field(description="실행 가능한 수정 제안 한 줄 — 계획자의 다음 입력이 된다")


class ValidationReport(BaseModel):
    """검증자의 출력 — 통과 여부와 위반 목록."""

    passed: bool
    violations: list[Violation] = []
