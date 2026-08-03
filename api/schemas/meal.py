"""계획자 계약서 — 모델의 출력을 자유 텍스트가 아니라 검증된 타입으로 받는다.

structured output은 "예쁘게 받기"가 아니라 파이프라인의 배선이다:
계획자의 출력(MealPlan)은 검증자의 입력이 되고, 그대로 API 응답으로
직렬화된다. 스키마 없이는 단계 간 연결 자체가 불가능하다.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Day = Literal["월", "화", "수", "목", "금", "토", "일"]
DAYS: tuple[str, ...] = ("월", "화", "수", "목", "금", "토", "일")


class PlanRequest(BaseModel):
    """POST /plan 입력 — UI 폼과 1:1 대응.

    틀린 타입은 LLM 요금이 나가기 전에 Pydantic이 공짜로 막는다. 첫 번째 관문.
    """

    kcal_min: int = 500
    kcal_max: int = 800
    sodium_limit_mg: int = 800  # 저녁 몫 나트륨 예산
    protein_min_g: int = 25
    request: str = ""  # 자유 요청사항 한 줄 — 비우면 순수 조건만으로 새로 생성. 상태는 이 필드가 전부


class Meal(BaseModel):
    day: Day
    food_code: str = Field(description="후보 목록의 식품코드를 글자 그대로 (예: D101-004310000-0001)")
    food_name: str = Field(description="후보 목록의 식품명을 글자 그대로 (예: 국밥_순대국밥)")
    serving_g: int = Field(description="1인분량(g) — 후보 목록의 값 그대로")
    calories_kcal: float = Field(description="1인분 환산 열량 = 100g 기준값 × serving_g ÷ 100")
    protein_g: float = Field(description="1인분 환산 단백질(g)")
    sodium_mg: float = Field(description="1인분 환산 나트륨(mg)")
    reason: str = Field(description="선정 이유 한 줄")


class MealPlan(BaseModel):
    """계획자의 출력 — 저녁 7끼."""

    meals: list[Meal] = Field(min_length=7, max_length=7, description="월~일 각각 정확히 한 끼")

    @model_validator(mode="after")
    def days_are_unique(self) -> "MealPlan":
        # 스키마 위반이면 instructor가 이 오류 문장을 모델에 되돌려 재요청한다
        days = [meal.day for meal in self.meals]
        if sorted(days) != sorted(DAYS):
            raise ValueError(f"요일은 월~일 각각 정확히 한 번이어야 한다. 현재: {days}")
        return self
