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


class MealPick(BaseModel):
    """**모델이 실제로 정하는 것** — 요일과 음식, 그리고 고른 이유뿐이다.

    v2.0까지는 모델이 식품명·1인분량·환산 열량·단백질·나트륨까지 다 채웠다.
    그런데 그 다섯 개는 전부 **식품코드만 알면 코드가 계산할 수 있는 값**이다.
    모델에게 시킨 것은 판단이 아니라 받아쓰기와 산수였고, 둘 다 모델이 제일
    못하는 일이다. 받아쓰기는 오탈자가 나고 산수는 틀린다.

    실제로 3주차의 검증자와 audit_plan()이 **그 산수를 다시 하고 있었다.**
    같은 계산을 두 번 하면서 한 번은 비싸게 하고 있었던 셈이다.

    v2.1은 모델에게 고르는 일만 맡긴다. **AI를 안 쓰는 결정도 설계다**의 다음 판.
    """

    day: Day
    food_code: str = Field(description="후보 목록의 식품코드를 글자 그대로 (예: D101-004310000-0001)")
    reason: str = Field(description="그 음식을 고른 이유 한 줄")


class Meal(BaseModel):
    """API가 내보내는 한 끼 — 모델의 선택(day·food_code·reason)에 **코드가 계산한**
    나머지를 채운 것이다. UI와 검증자가 보는 모양은 v1.5부터 그대로다."""

    day: Day
    food_code: str
    food_name: str
    serving_g: int
    calories_kcal: float
    protein_g: float
    sodium_mg: float
    reason: str


class MealDraft(BaseModel):
    """모델의 출력 — 어떤 요일에 어떤 음식을 왜 골랐는가. 숫자는 없다."""

    meals: list[MealPick] = Field(min_length=7, max_length=7,
                                  description="월~일 각각 정확히 한 끼")

    @model_validator(mode="after")
    def codes_are_from_the_list(self, info) -> "MealDraft":
        """후보 목록에 없는 식품코드는 **지어낸 것**이다.

        실제로 잡혔다. 모델이 `D101-051200`처럼 뒤가 잘린 코드를 냈다.
        v2.0까지는 이름과 숫자를 함께 받아써서 이런 오류가 "이름은 맞는데 코드가
        이상한" 모양으로 섞여 들어왔고, 검증자가 환산을 대조하다 뒤늦게 걸렸다.
        이제 모델이 내는 것이 코드 하나뿐이라 **여기서 즉시 판정된다.**

        예외를 올리면 instructor가 이 메시지를 담아 같은 모델에 다시 묻는다
        (형식 층 재시도). 수정 루프를 한 바퀴 돌리는 것보다 싸다.
        """
        allowed = (info.context or {}).get("allowed_codes")
        if not allowed:
            return self  # 후보를 안 넘겨준 호출(테스트 등)에서는 검사하지 않는다
        unknown = [m.food_code for m in self.meals if m.food_code not in allowed]
        if unknown:
            raise ValueError(
                f"후보 목록에 없는 식품코드입니다: {', '.join(unknown)}. "
                "목록의 코드를 글자 그대로 옮겨라."
            )
        return self


class MealPlan(BaseModel):
    """계획자의 출력 — 저녁 7끼. 숫자는 코드가 채운 뒤의 모양이다."""

    meals: list[Meal] = Field(min_length=7, max_length=7, description="월~일 각각 정확히 한 끼")

    @model_validator(mode="after")
    def days_are_unique(self) -> "MealPlan":
        # 스키마 위반이면 instructor가 이 오류 문장을 모델에 되돌려 재요청한다
        days = [meal.day for meal in self.meals]
        if sorted(days) != sorted(DAYS):
            raise ValueError(f"요일은 월~일 각각 정확히 한 번이어야 한다. 현재: {days}")
        return self
