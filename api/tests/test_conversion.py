"""환산·크로스체크 유닛테스트 — LLM 없이 도는 산수의 안전망."""

from pipeline.crosscheck import audit_plan, crosscheck_plan, per_serving, weekly_sodium_mg
from schemas.meal import DAYS, Meal, MealPlan, PlanRequest

FOODS = {
    "D101-004310000-0001": {
        "food_code": "D101-004310000-0001",
        "food_name": "국밥_순대국밥",
        "energy_kcal_100g": 75,
        "protein_g_100g": 4.5,
        "sodium_mg_100g": 470,
        "serving_g": 900,
    },
    "D101-001000000-0001": {
        "food_code": "D101-001000000-0001",
        "food_name": "비빔밥_전주비빔밥",
        "energy_kcal_100g": 128,
        "protein_g_100g": 5.6,
        "sodium_mg_100g": 150,
        "serving_g": 480,
    },
}


def make_meal(day: str, code: str = "D101-001000000-0001", **overrides) -> Meal:
    food = FOODS[code]
    fields = {
        "day": day,
        "food_code": code,
        "food_name": food["food_name"],
        "serving_g": food["serving_g"],
        "calories_kcal": per_serving(food["energy_kcal_100g"], food["serving_g"]),
        "protein_g": per_serving(food["protein_g_100g"], food["serving_g"]),
        "sodium_mg": per_serving(food["sodium_mg_100g"], food["serving_g"]),
        "reason": "테스트",
    }
    fields.update(overrides)
    return Meal(**fields)


def test_serving_conversion():
    """교안의 그 숫자들: 순대국밥 675kcal·4,230mg, 김밥 322kcal·706.1mg."""
    assert per_serving(75, 900) == 675.0
    assert per_serving(470, 900) == 4230.0
    assert per_serving(140, 230) == 322.0
    assert per_serving(307, 230) == 706.1


def test_weekly_sodium_sum():
    plan = MealPlan(meals=[make_meal(day) for day in DAYS])
    assert weekly_sodium_mg(plan, FOODS) == 720.0 * 7


def test_crosscheck_passes_clean_plan():
    plan = MealPlan(meals=[make_meal(day) for day in DAYS])
    violations = crosscheck_plan(PlanRequest(), plan, FOODS)
    # 비빔밥 7회는 중복 위반 — 그것만 남아야 한다
    assert {v.type for v in violations} == {"중복"}


def test_crosscheck_catches_sodium_bomb():
    """화요일 순대국밥: 470mg/100g × 900g = 4,230mg > 800mg."""
    meals = [make_meal(day) for day in DAYS[:1]] + [make_meal("화", "D101-004310000-0001")] + [
        make_meal(day) for day in DAYS[2:]
    ]
    violations = crosscheck_plan(PlanRequest(), MealPlan(meals=meals), FOODS)
    sodium = [v for v in violations if v.type == "제약_위반" and "나트륨" in v.evidence]
    assert len(sodium) == 1
    assert sodium[0].day == "화"
    assert sodium[0].severity == "high"
    assert "4,230mg" in sodium[0].evidence


def test_crosscheck_catches_wrong_conversion():
    """계획자가 100g값을 그대로 써내는 고전적 실수를 잡는다."""
    meals = [make_meal("월", calories_kcal=128.0)] + [make_meal(day) for day in DAYS[1:]]
    violations = crosscheck_plan(PlanRequest(), MealPlan(meals=meals), FOODS)
    assert any(v.type == "환산_오류" and "calories_kcal" in v.evidence for v in violations)


def test_audit_reports_per_criterion_verdicts():
    """감사는 "통과"의 근거다 — 끼니별 기준 판정과 전체 규칙 판정이 수치와 함께 남는다."""
    meals = [make_meal(day) for day in DAYS[:1]] + [make_meal("화", "D101-004310000-0001")] + [
        make_meal(day) for day in DAYS[2:]
    ]
    audit = audit_plan(PlanRequest(), MealPlan(meals=meals), FOODS)

    tue = next(m for m in audit.meals if m.day == "화")
    assert tue.exists and tue.conversion_ok
    assert tue.kcal == 675.0 and tue.kcal_ok  # 500~800 안
    assert tue.sodium_mg == 4230.0 and not tue.sodium_ok  # 한도 800 초과

    mon = next(m for m in audit.meals if m.day == "월")
    assert mon.sodium_ok and mon.kcal_ok and mon.protein_ok

    assert audit.days_complete is True
    assert audit.rep_counts["비빔밥"] == 6
    assert audit.repetition_ok is False  # 주 2회 초과
    assert audit.constraints.sodium_limit_mg == 800


def test_crosscheck_catches_nonexistent_food():
    fake = make_meal("월")
    fake = fake.model_copy(update={"food_code": "D101-999999999-0001", "food_name": "샐러드_연어스테이크"})
    meals = [fake] + [make_meal(day) for day in DAYS[1:]]
    violations = crosscheck_plan(PlanRequest(), MealPlan(meals=meals), FOODS)
    assert any(v.type == "존재하지_않는_음식" and v.day == "월" for v in violations)
