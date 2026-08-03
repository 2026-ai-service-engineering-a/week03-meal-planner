"""수정 루프 — 파이프라인의 지휘부. 순서·반복·종료가 전부 이 코드에 있다.

에이전트가 아니다: 몇 번 돌지(최대 3회), 언제 멈출지(통과 또는 상한)를 모델이
아니라 이 for문이 정한다. 모델은 각 단계의 일꾼일 뿐이다.

계획 → 검증(다른 회사 LLM) → 크로스체크(코드 재계산) → 병합 → 통과면 종료,
아니면 위반 목록을 계획자에 회신하고 재계획. 3회 안에 통과 못 하면 마지막 안과
위반 목록을 함께 반환한다 — 정직한 실패.
"""

from llm.client import log
from pipeline.crosscheck import crosscheck_plan
from pipeline.foods import load_foods, select_candidates
from pipeline.planner import plan_meals
from pipeline.validator import validate_plan
from schemas.meal import PlanRequest
from schemas.validation import AttemptRecord, PlanResponse, ValidationReport, Violation

MAX_ATTEMPTS = 3  # 루프 가드 — 파이프라인에도 상한이 있다 (2주차 에이전트 루프 가드의 재회)


def merge_reports(llm_report: ValidationReport, code_violations: list[Violation]) -> ValidationReport:
    """검증자 판정과 코드 재계산을 병합한다. 같은 (유형, 요일, 코드)면 검증자 것을 남긴다
    (해석·제안의 품질이 값어치). 코드만 잡은 위반은 뒤에 붙는다 — 산수는 코드가 맞다."""
    seen = {(v.type, v.day, v.food_code) for v in llm_report.violations}
    code_only = [v for v in code_violations if (v.type, v.day, v.food_code) not in seen]
    if code_only:
        log.info(
            "CROSSCHECK ─ 검증자 %d건, 코드가 추가로 잡은 위반 %d건 (누가 맞나? 산수는 코드가 맞다)",
            len(llm_report.violations),
            len(code_only),
        )
    merged = [*llm_report.violations, *code_only]
    return ValidationReport(passed=not merged, violations=merged)


def run_pipeline(req: PlanRequest) -> PlanResponse:
    foods = load_foods()
    candidates = select_candidates(req, foods)

    history: list[AttemptRecord] = []
    violations: list[Violation] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        plan = plan_meals(req, candidates, violations=violations or None)
        report = merge_reports(validate_plan(req, plan), crosscheck_plan(req, plan, foods))
        history.append(AttemptRecord(attempt=attempt, passed=report.passed, violations=report.violations))

        if report.passed:
            break
        log.info("LOOP ─ attempt %d/%d 미통과 (위반 %d건) — 위반 목록을 계획자에 회신", attempt, MAX_ATTEMPTS, len(report.violations))
        violations = report.violations

    return PlanResponse(meals=plan.meals, report=report, attempts=len(history), history=history)
