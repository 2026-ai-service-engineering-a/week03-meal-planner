"""계획자 — 조건으로 foods.json을 거르고, 텍스트로 펴서, 계약과 함께 보낸다.

이 함수가 계획자의 전부다. 0단의 세 실패에 대한 처방이 전부 여기 있다:
  환각     → 후보 목록 컨텍스트 주입 ("목록에 없는 음식 금지")
  형식 붕괴 → instructor 스키마 강제 (response_model=MealPlan)
  수치 창작 → 환산 규칙 + few-shot 예시 (그래도 틀린다 — 그래서 2회전에 크로스체크가 온다)

모델이 보는 것은 객체가 아니라 결국 여기서 조립되는 텍스트다.
"""

from llm.client import structured_complete
from schemas.meal import MealPlan, PlanRequest
from schemas.validation import Violation

SYSTEM_PROMPT = """너는 식단 계획자다. 저녁 7끼(월~일)를 짠다.

규칙:
- 반드시 후보 목록 안의 음식만 쓴다. 목록에 없는 음식 금지.
  food_code·food_name·serving_g는 목록의 값을 글자 그대로 옮긴다.
- 영양값은 100g 기준이다. 1인분 환산값 = 100g값 × serving_g ÷ 100 을 계산해
  calories_kcal·protein_g·sodium_mg에 넣는다.
- 제약: 한 끼 {kcal_min}~{kcal_max}kcal, 단백질 {protein_min_g}g 이상,
  나트륨 {sodium_limit_mg}mg 이내, 같은 대표식품(food_name의 '_' 앞부분)은 주 2회 이하.
- 요일은 월~일 각각 정확히 한 번.
- reason에는 그 음식을 고른 이유를 한 줄로 쓴다.

환산 예시:
- 순대국밥 75kcal/100g, 1인분 900g → 75 × 900 ÷ 100 = 675 kcal.
  나트륨 470mg/100g → 470 × 900 ÷ 100 = 4,230 mg
- 김밥 140kcal/100g, 1인분 230g → 140 × 230 ÷ 100 = 322 kcal.
  나트륨 307mg/100g → 307 × 230 ÷ 100 = 706.1 mg"""


def _candidate_lines(candidates: list[dict]) -> str:
    """후보 목록을 프롬프트에 박는다 — 환각이 억제되는 이유가 이 텍스트다."""
    return "\n".join(
        f"[{food['food_code']} {food['food_name']} ({food['category']}) "
        f"{food['energy_kcal_100g']}kcal/100g 단백질{food['protein_g_100g']}g "
        f"나트륨{food['sodium_mg_100g']}mg 1인분{food['serving_g']}g]"
        for food in candidates
    )


def _violation_lines(violations: list[Violation]) -> str:
    """검증자의 evidence·suggestion 필드가 계획자의 입력 문장이 되는 순간."""
    return "\n".join(
        f"- [{v.severity}] {v.day}요일 {v.food_code}: {v.evidence}\n  제안: {v.suggestion}"
        for v in violations
    )


def plan_meals(
    req: PlanRequest,
    candidates: list[dict],
    violations: list[Violation] | None = None,
) -> MealPlan:
    system = SYSTEM_PROMPT.format(
        kcal_min=req.kcal_min,
        kcal_max=req.kcal_max,
        protein_min_g=req.protein_min_g,
        sodium_limit_mg=req.sodium_limit_mg,
    )
    user = f"조건: {req.kcal_min}~{req.kcal_max}kcal, 나트륨≤{req.sodium_limit_mg}mg, 단백질≥{req.protein_min_g}g"
    if req.request:
        user += f"\n요청사항: {req.request}"
    user += f"\n\n후보 목록:\n{_candidate_lines(candidates)}"

    label = ""
    if violations:
        label = "(revision)"
        user += f"\n\n직전 식단에서 다음 위반이 발견됨. 반영해서 다시 계획해라:\n{_violation_lines(violations)}"

    return structured_complete(
        "planner",
        MealPlan,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        label=label,
    )
