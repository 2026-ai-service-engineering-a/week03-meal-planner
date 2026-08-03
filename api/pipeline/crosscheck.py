"""코드 크로스체크 — 믿되, 재계산하라.

산수만 필요하면 LLM 검증자는 필요 없다. 코드가 더 정확하다. LLM 검증자의
값어치는 위반의 해석·심각도 판단·수정 제안이고, 환산·합계 같은 산수는 여기
순수 함수가 이중으로 보증한다. LLM 없이 도는 산수 — 유닛테스트가 붙는 곳이다.
"""

from collections import Counter

from schemas.meal import Meal, MealPlan, PlanRequest
from schemas.validation import Violation

TOLERANCE = 0.05  # 환산 기재값 허용 오차 5% — 반올림 차이는 봐준다


def per_serving(value_per_100g: float, serving_g: float) -> float:
    """100g 기준값 → 1인분 환산. 이 한 줄이 LLM이 정확히 틀리는 지점이다."""
    return round(value_per_100g * serving_g / 100, 1)


def weekly_sodium_mg(plan: MealPlan, foods: dict[str, dict]) -> float:
    """주간 나트륨 합계 — 원본 수치로 재계산한 값의 합."""
    return round(
        sum(
            per_serving(foods[meal.food_code]["sodium_mg_100g"], meal.serving_g)
            for meal in plan.meals
            if meal.food_code in foods
        ),
        1,
    )


def representative(name: str) -> str:
    """대표식품 = food_name의 '_' 앞부분 (예: 국밥_순대국밥 → 국밥)."""
    return name.split("_")[0]


def _conversion_violations(meal: Meal, food: dict) -> list[Violation]:
    """계획자가 써낸 환산값을 원본 100g값으로 재계산해 대조한다."""
    found = []
    if meal.serving_g != food["serving_g"]:
        found.append(
            Violation(
                type="환산_오류",
                day=meal.day,
                food_code=meal.food_code,
                evidence=f"{meal.food_name} 1인분량은 {food['serving_g']}g인데 {meal.serving_g}g로 기재",
                severity="medium",
                suggestion="serving_g는 원본 1인분량 그대로 옮길 것",
            )
        )
    checks = (
        ("calories_kcal", "energy_kcal_100g", "kcal", meal.calories_kcal),
        ("protein_g", "protein_g_100g", "g", meal.protein_g),
        ("sodium_mg", "sodium_mg_100g", "mg", meal.sodium_mg),
    )
    for field, source_key, unit, actual in checks:
        expected = per_serving(food[source_key], meal.serving_g)
        if abs(actual - expected) > TOLERANCE * max(expected, 1.0):
            found.append(
                Violation(
                    type="환산_오류",
                    day=meal.day,
                    food_code=meal.food_code,
                    evidence=(
                        f"{meal.food_name} {field}: {food[source_key]}{unit}/100g × {meal.serving_g}g"
                        f" = {expected}{unit} ≠ 기재값 {actual}{unit}"
                    ),
                    severity="medium",
                    suggestion=f"{field}를 재계산값 {expected}{unit}로 수정",
                )
            )
    return found


def _constraint_violations(req: PlanRequest, meal: Meal, food: dict) -> list[Violation]:
    """제약 판정은 모델이 써낸 값이 아니라 재계산값 기준이다."""
    found = []
    kcal = per_serving(food["energy_kcal_100g"], meal.serving_g)
    protein = per_serving(food["protein_g_100g"], meal.serving_g)
    sodium = per_serving(food["sodium_mg_100g"], meal.serving_g)

    if sodium > req.sodium_limit_mg:
        found.append(
            Violation(
                type="제약_위반",
                day=meal.day,
                food_code=meal.food_code,
                evidence=(
                    f"{meal.food_name} 나트륨 {food['sodium_mg_100g']}mg/100g × {meal.serving_g}g"
                    f" = {sodium:,.0f}mg > 한도 {req.sodium_limit_mg}mg"
                ),
                severity="high",  # 나트륨 예산이 이 서비스의 존재 이유다
                suggestion="나트륨이 낮은 음식(국물이 적은 밥류·구이류 등)으로 교체",
            )
        )
    if not req.kcal_min <= kcal <= req.kcal_max:
        found.append(
            Violation(
                type="제약_위반",
                day=meal.day,
                food_code=meal.food_code,
                evidence=f"{meal.food_name} 열량 {kcal}kcal — 범위 {req.kcal_min}~{req.kcal_max}kcal 벗어남",
                severity="medium",
                suggestion="열량이 범위 안인 음식으로 교체",
            )
        )
    if protein < req.protein_min_g:
        found.append(
            Violation(
                type="제약_위반",
                day=meal.day,
                food_code=meal.food_code,
                evidence=f"{meal.food_name} 단백질 {protein}g < 최소 {req.protein_min_g}g",
                severity="medium",
                suggestion="단백질이 많은 음식(구이·찜류 등)으로 교체",
            )
        )
    return found


def crosscheck_plan(req: PlanRequest, plan: MealPlan, foods: dict[str, dict]) -> list[Violation]:
    """존재 → 환산 → 제약 → 중복 순으로 전 끼니를 재검한다."""
    violations: list[Violation] = []
    for meal in plan.meals:
        food = foods.get(meal.food_code)
        if food is None:
            violations.append(
                Violation(
                    type="존재하지_않는_음식",
                    day=meal.day,
                    food_code=meal.food_code,
                    evidence=f"{meal.food_name}({meal.food_code})는 foods.json에 없다",
                    severity="high",
                    suggestion="후보 목록에 있는 음식으로 교체",
                )
            )
            continue
        violations += _conversion_violations(meal, food)
        violations += _constraint_violations(req, meal, food)

    counts = Counter(representative(meal.food_name) for meal in plan.meals)
    for rep, count in counts.items():
        if count > 2:
            day = next(m.day for m in plan.meals if representative(m.food_name) == rep)
            violations.append(
                Violation(
                    type="중복",
                    day=day,
                    food_code=next(m.food_code for m in plan.meals if representative(m.food_name) == rep),
                    evidence=f"대표식품 '{rep}' 주 {count}회 — 같은 대표식품은 주 2회 이하",
                    severity="low",
                    suggestion=f"'{rep}' 중 일부를 다른 대표식품으로 교체",
                )
            )
    return violations
