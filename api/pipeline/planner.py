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
from schemas.meal import Meal, MealDraft, MealPlan, PlanRequest
from schemas.validation import Violation

SYSTEM_PROMPT = """너는 식단 계획자다. 저녁 7끼(월~일)를 짠다.

규칙:
- 반드시 후보 목록 안의 음식만 쓴다. 목록에 없는 음식 금지.
  food_code는 목록의 값을 글자 그대로 옮긴다.
- 목록의 숫자는 **이미 1인분 기준으로 환산된 값**이다. 계산하지 마라.
- 제약: 한 끼 {kcal_min}~{kcal_max}kcal, 단백질 {protein_min_g}g 이상,
  나트륨 {sodium_limit_mg}mg 이내, 같은 대표식품(이름의 '_' 앞부분)은 주 2회 이하.
- 요청사항은 제약을 어기지 않는 범위에서만 반영한다. 둘이 충돌하면 제약이 이긴다.
- 요일은 월~일 각각 정확히 한 번.
- reason에는 그 음식을 고른 이유를 한 줄로 쓴다.

너는 **고르기만 한다.** 식품명·1인분량·영양 수치는 코드가 식품코드로 채운다."""


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


def per_serving(food: dict) -> dict:
    """100g 기준값을 1인분으로 환산한다. **코드가 한다.**

    이 함수가 v2.1의 전부다. 여기서 계산한 값을 후보 줄에 실으면 모델은
    비교만 하면 되고, 같은 값을 응답에 다시 채워 넣을 필요도 없다.
    """
    factor = food["serving_g"] / 100
    return {
        "serving_g": int(food["serving_g"]),
        "calories_kcal": round(food["energy_kcal_100g"] * factor, 1),
        "protein_g": round(food["protein_g_100g"] * factor, 2),
        "sodium_mg": round(food["sodium_mg_100g"] * factor, 1),
    }


def _candidate_lines(candidates: list[dict], docs: dict[str, dict] | None = None) -> str:
    """후보 목록을 프롬프트에 박는다 — 환각이 억제되는 이유가 이 텍스트다.

    **v2.1부터 숫자가 1인분 기준이다.** 예전에는 100g 기준값과 1인분량을 나란히
    주고 모델이 곱하게 했다. 그 곱셈이 계획자 출력 토큰의 대부분이었고, 틀리면
    검증자가 잡아 루프가 한 바퀴 더 돌았다. 미리 계산해 주면 그 일이 통째로 없어진다.
    """
    docs = docs or {}
    lines = []
    for food in candidates:
        v = per_serving(food)
        line = (f"[{food['food_code']} {food['food_name']} "
                f"1인분{v['serving_g']}g {v['calories_kcal']}kcal "
                f"단백질{v['protein_g']}g 나트륨{v['sodium_mg']}mg]")
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

    draft = structured_complete(
        "planner",
        MealDraft,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        label=label,
        recorder=recorder,
        validation_context={"allowed_codes": {f["food_code"] for f in candidates}},
    )
    return _fill(draft, candidates)


def _fill(draft: MealDraft, candidates: list[dict]) -> MealPlan:
    """모델이 고른 식품코드에 **코드가 나머지를 채운다.**

    모델이 목록 밖의 코드를 지어내면 여기서 걸린다. v2.0까지는 이름과 숫자를
    모델이 받아쓰다 조용히 틀릴 수 있었지만, 이제 틀릴 자리가 코드 하나뿐이고
    그건 딕셔너리 조회 한 번으로 판정된다. **환각의 표면적이 줄었다.**
    """
    by_code = {food["food_code"]: food for food in candidates}
    meals = []
    for pick in draft.meals:
        food = by_code.get(pick.food_code)
        if food is None:
            # 여기까지 오면 스키마 검증(codes_are_from_the_list)이 이미 막았어야 한다.
            # 재시도를 다 쓰고도 지어낸 코드가 남은 경우라 정직하게 올린다
            raise ValueError(f"후보 목록에 없는 식품코드입니다: {pick.food_code}")
        meals.append(Meal(day=pick.day, food_code=food["food_code"],
                          food_name=food["food_name"], reason=pick.reason,
                          **per_serving(food)))
    return MealPlan(meals=meals)
