"""검증자 — 계획자와 같은 모양, 다른 회사. 교차 검증이 코드에서는 이 한 줄 차이다.

만든 쪽이 검사하면 안 된다 — 자기 출력에 후한 self-preference bias의 LLM 버전.
그래서 VALIDATOR_MODEL은 계획자와 다른 프로바이더로 두고, 죽으면
VALIDATOR_FALLBACKS 순서로 넘어간다. 계획자가 죽으면 "식단이 안 나옴"(불편)
이지만 검증자가 죽으면 "검증 안 된 식단이 나감"(위험)이라, 폴백은 여기 붙는다.

입력은 1회전의 출력 객체(MealPlan)가 텍스트로 풀린 것 — 스키마가 배선이라는
말의 실물이다. 각 음식의 원본 100g 영양값·1인분량을 함께 줘서 대조하게 한다.
"""

from llm.client import structured_complete
from pipeline.foods import load_foods
from schemas.llm import LlmCall
from schemas.meal import MealPlan, PlanRequest
from schemas.validation import ValidationReport

SYSTEM_PROMPT = """너는 식단 검증자다. 계획자가 짠 식단을 원본 수치와 대조해 위반을 찾는다.

검사 항목 (type):
- 존재하지_않는_음식: food_code가 원본 수치 목록에 없다
- 환산_오류: calories_kcal·protein_g·sodium_mg가 100g값 × serving_g ÷ 100 재계산과 다르다
- 제약_위반: 한 끼 {kcal_min}~{kcal_max}kcal, 단백질 {protein_min_g}g 이상,
  나트륨 {sodium_limit_mg}mg 이내를 어겼다 (판정은 재계산값 기준)
- 중복: 같은 대표식품(food_name의 '_' 앞부분)이 주 3회 이상 나온다

규칙:
- 위반만 보고한다. 재계산값과 기재값이 일치하면 그 항목은 violations에 넣지 않는다.
  "일치 확인"은 위반이 아니다.
- evidence에는 판정 근거를 계산식으로 쓴다.
  예: "순대국밥 나트륨 470mg/100g × 900g = 4,230mg > 한도 800mg"
- suggestion에는 실행 가능한 수정 제안을 한 줄로 쓴다.
  예: "국물이 적은 밥류(비빔밥 등)로 교체"
- 위반이 하나도 없으면 passed=true, violations=[]"""


def _plan_lines(plan: MealPlan) -> str:
    return "\n".join(
        f"{meal.day} {meal.food_name}({meal.food_code}) serving {meal.serving_g}g "
        f"칼로리 {meal.calories_kcal} 단백질 {meal.protein_g} 나트륨 {meal.sodium_mg}"
        for meal in plan.meals
    )


def _source_lines(plan: MealPlan, foods: dict[str, dict]) -> str:
    """식단에 등장한 음식의 원본 수치 — 대조 없는 검증은 감상일 뿐이다."""
    lines = []
    for code in dict.fromkeys(meal.food_code for meal in plan.meals):  # 순서 유지 중복 제거
        food = foods.get(code)
        if food is None:
            lines.append(f"{code}: 원본 목록에 없음 — 존재하지 않는 음식")
            continue
        lines.append(  # food_code를 함께 줘야 검증자가 코드 실존을 대조할 수 있다
            f"{food['food_name']}({food['food_code']}) 100g당 {food['energy_kcal_100g']}kcal, "
            f"단백질 {food['protein_g_100g']}g, Na {food['sodium_mg_100g']}mg, "
            f"1인분량 {food['serving_g']}g"
        )
    return "\n".join(lines)


def validate_plan(
    req: PlanRequest,
    plan: MealPlan,
    recorder: list[LlmCall] | None = None,
) -> ValidationReport:
    foods = load_foods()
    system = SYSTEM_PROMPT.format(
        kcal_min=req.kcal_min,
        kcal_max=req.kcal_max,
        protein_min_g=req.protein_min_g,
        sodium_limit_mg=req.sodium_limit_mg,
    )
    user = f"식단:\n{_plan_lines(plan)}\n\n원본 수치:\n{_source_lines(plan, foods)}"
    return structured_complete(
        "validator",
        ValidationReport,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        recorder=recorder,
    )
