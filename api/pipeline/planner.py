"""계획자 — 조건으로 foods.json을 거르고, 텍스트로 펴서, 계약과 함께 보낸다.

이 함수가 계획자의 전부다. 0단의 세 실패에 대한 처방이 전부 여기 있다:
  환각     → 후보 목록 컨텍스트 주입 ("목록에 없는 음식 금지")
  형식 붕괴 → instructor 스키마 강제 (response_model=MealPlan)
  수치 창작 → 환산 규칙 + few-shot 예시 (그래도 틀린다 — 그래서 2회전에 크로스체크가 온다)

모델이 보는 것은 객체가 아니라 결국 여기서 조립되는 텍스트다.
"""

import os

from llm.client import structured_complete
from pipeline.foods import load_docs
from schemas.llm import LlmCall
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
- 요청사항은 제약을 어기지 않는 범위에서만 반영한다. 둘이 충돌하면 제약이 이긴다.
- 요일은 월~일 각각 정확히 한 번.
- reason에는 그 음식을 고른 이유를 한 줄로 쓴다.

환산 예시:
- 순대국밥 75kcal/100g, 1인분 900g → 75 × 900 ÷ 100 = 675 kcal.
  나트륨 470mg/100g → 470 × 900 ÷ 100 = 4,230 mg
- 김밥 140kcal/100g, 1인분 230g → 140 × 230 ÷ 100 = 322 kcal.
  나트륨 307mg/100g → 307 × 230 ÷ 100 = 706.1 mg"""


# 후보 줄에 설명문을 몇 자까지 실을 것인가. **기본은 0 — 아예 안 싣는다.**
#
# 설명문은 **검색 단계에서 이미 자기 일을 다 했다.** 500자 전문으로 벡터를 만들어
# 210건을 40건으로 줄였고, 그 뒤에 같은 텍스트를 프롬프트에 또 넣을 이유가 없다.
# 색인은 색인이고 원본은 원본이다 — 모델에게 보낼 값은 foods.json의 숫자다.
#
# 처음에는 60자를 실었다. 재보니 후보 블록 6,087자 중 2,629자(44%)가 설명이었고,
# 빼도 검증 통과율은 3/3 그대로에 평균 84.7초 → 63.2초로 오히려 빨라졌다.
# **검색에 쓰는 텍스트와 프롬프트에 넣는 텍스트는 같을 필요가 없다.**
#
# 0이 아닌 값을 주면 다시 실린다. 설명이 값을 하는지 직접 재보라는 손잡이다.
DOC_SNIPPET = int(os.environ.get("DOC_SNIPPET", "0"))


def _candidate_lines(candidates: list[dict], docs: dict[str, dict] | None = None) -> str:
    """후보 목록을 프롬프트에 박는다 — 환각이 억제되는 이유가 이 텍스트다.

    v2.0부터 숫자 옆에 **뜻**이 한 조각 붙는다. 검색이 이미 요청에 가까운 것만
    골라 왔지만, 고르는 이유(reason)를 쓰려면 모델도 그 음식이 뭔지 알아야 한다.
    """
    docs = docs or {}
    lines = []
    for food in candidates:
        line = (f"[{food['food_code']} {food['food_name']} "
                f"{food['energy_kcal_100g']}kcal/100g 단백질{food['protein_g_100g']}g "
                f"나트륨{food['sodium_mg_100g']}mg 1인분{food['serving_g']}g]")
        text = (docs.get(food["food_code"]) or {}).get("text", "")
        if text:
            line += " " + text[:DOC_SNIPPET].rstrip()
        lines.append(line)
    return "\n".join(lines)


def _violation_lines(violations: list[Violation]) -> str:
    """검증자의 evidence·suggestion 필드가 계획자의 입력 문장이 되는 순간."""
    return "\n".join(
        f"- [{v.severity}] {v.day}요일 {v.food_code}: {v.evidence}\n  제안: {v.suggestion}"
        for v in violations
    )


def _meal_lines(plan: MealPlan) -> str:
    return "\n".join(
        f"{meal.day} {meal.food_name}({meal.food_code}) {meal.serving_g}g "
        f"{meal.calories_kcal}kcal 단백질{meal.protein_g}g 나트륨{meal.sodium_mg}mg"
        for meal in plan.meals
    )


def plan_meals(
    req: PlanRequest,
    candidates: list[dict],
    violations: list[Violation] | None = None,
    previous: MealPlan | None = None,
    recorder: list[LlmCall] | None = None,
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
    user += f"\n\n후보 목록:\n{_candidate_lines(candidates, load_docs())}"

    label = ""
    if violations:
        label = "(revision)"
        if previous is not None:
            user += f"\n\n직전 식단:\n{_meal_lines(previous)}"
        user += (
            "\n\n직전 식단에서 다음 위반이 발견됨. 위반이 없는 끼니는 그대로 유지하고,"
            " 위반이 있는 끼니만 제약을 지키는 음식으로 교체해라."
            " 위반 해소가 요청사항보다 우선한다:\n"
            f"{_violation_lines(violations)}"
        )

    return structured_complete(
        "planner",
        MealPlan,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        label=label,
        recorder=recorder,
    )
