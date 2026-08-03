"""검증자 계약서 — 판정에 근거를 강제한다.

"나트륨 초과 같아요"가 아니라 "470mg/100g × 900g = 4,230mg > 800mg"을
내놓게 하는 것이 evidence 필드다. structured output이 판정의 품질을 끌어올린다.

검증자의 출력은 사람이 읽는 글이 아니라 다음 코드가 소비하는 데이터다:
passed 하나로 코드가 루프를 돌릴지 결정하고, violations의 evidence·suggestion은
계획자의 다음 프롬프트에 그대로 들어간다. 검증자 출력이 계획자 입력이 되는 배선.
"""

from typing import Literal

from pydantic import BaseModel, Field

from schemas.meal import Meal, PlanRequest


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


class AttemptRecord(BaseModel):
    """수정 루프의 시도 1회 — 루프가 돌았다는 증거가 응답에 남는다."""

    attempt: int
    passed: bool
    violations: list[Violation]


class MealAudit(BaseModel):
    """한 끼 감사 — 코드가 재계산한 수치와 기준별 판정.

    "통과"가 감상이 아니라 무엇을 확인한 결과인지 보여준다. 수치는 전부
    crosscheck의 순수 함수 재계산이다 (LLM 아님).
    """

    day: str
    food_name: str
    food_code: str
    exists: bool  # foods.json에 실존하는 코드인가
    conversion_ok: bool  # 계획자가 써낸 환산값이 재계산과 일치하는가
    kcal: float  # 이하 재계산값 (1인분 기준)
    kcal_ok: bool
    protein_g: float
    protein_ok: bool
    sodium_mg: float
    sodium_ok: bool


class PlanAudit(BaseModel):
    """식단 전체 감사 — 검증 리포트의 산수 근거."""

    constraints: PlanRequest  # 이번 판정에 적용된 기준 (요청의 에코)
    meals: list[MealAudit]
    days_complete: bool  # 월~일 각각 정확히 한 끼인가
    rep_counts: dict[str, int]  # 대표식품별 등장 횟수
    repetition_ok: bool  # 전부 주 2회 이하인가
    weekly_sodium_mg: float


class PlanResponse(BaseModel):
    """최종 응답 = 식단 + 검증 리포트 + 시도 횟수 + 시도별 위반 이력 + 감사.

    3회 안에 통과 못 하면 passed=false인 리포트와 마지막 안이 그대로 나간다 —
    정직한 실패.
    """

    meals: list[Meal]
    report: ValidationReport
    attempts: int
    history: list[AttemptRecord]
    audit: PlanAudit  # 최종 식단에 대한 코드 재계산 근거
