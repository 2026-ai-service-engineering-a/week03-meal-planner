"""수정 루프 — 파이프라인의 지휘부. 순서·반복·종료가 전부 이 코드에 있다.

에이전트가 아니다: 몇 번 돌지(최대 3회), 언제 멈출지(통과 또는 상한)를 모델이
아니라 이 for문이 정한다. 모델은 각 단계의 일꾼일 뿐이다.

계획 → 검증(다른 회사 LLM) → 크로스체크(코드 재계산) → 병합 → 통과면 종료,
아니면 위반 목록을 계획자에 회신하고 재계획. 3회 안에 통과 못 하면 마지막 안과
위반 목록을 함께 반환한다 — 정직한 실패.
"""

from collections.abc import Iterator

from llm.client import log
from pipeline.crosscheck import audit_plan, crosscheck_plan
from pipeline.foods import load_foods, load_docs, select_candidates
from pipeline.retrieval import narrow
from pipeline.planner import plan_meals
from pipeline.validator import validate_plan
from schemas.llm import LlmCall
from schemas.meal import PlanRequest
from schemas.validation import AttemptRecord, PlanResponse, ValidationReport, Violation

MAX_ATTEMPTS = 3  # 루프 가드 — 파이프라인에도 상한이 있다 (2주차 에이전트 루프 가드의 재회)


def merge_reports(llm_report: ValidationReport, code_violations: list[Violation]) -> ValidationReport:
    """검증자 판정과 코드 재계산을 병합한다 — 판정은 코드가, 문장은 검증자가.

    네 위반 유형(존재·환산·제약·중복)은 전부 코드가 재검 가능하므로, 검증자의
    주장은 코드 재계산으로 확인될 때만 인정한다 (누가 맞나? 산수는 코드가 맞다).
    리허설에서 검증자가 "한도 내"를 위반으로, "주 2회 이하"를 중복으로 보고하는
    노이즈가 수정 루프를 오염시키는 것을 봤다 — 이 필터가 그 방어다.
    같은 (유형, 요일, 코드)면 검증자 것을 남긴다: 해석·심각도·수정 제안의 품질이
    LLM 검증자의 값어치다. 코드만 잡은 위반은 뒤에 붙는다.
    """
    code_keys = {(v.type, v.day, v.food_code) for v in code_violations}
    kept = [v for v in llm_report.violations if (v.type, v.day, v.food_code) in code_keys]
    dropped = len(llm_report.violations) - len(kept)

    seen = {(v.type, v.day, v.food_code) for v in kept}
    code_only = [v for v in code_violations if (v.type, v.day, v.food_code) not in seen]
    if dropped or code_only:
        log.info(
            "CROSSCHECK ─ 검증자 %d건 중 재계산 불일치 %d건 폐기, 코드가 추가로 잡은 위반 %d건 (산수는 코드가 맞다)",
            len(llm_report.violations),
            dropped,
            len(code_only),
        )
    merged = [*kept, *code_only]
    return ValidationReport(passed=not merged, violations=merged)


def _progress(stage: str, detail: str, attempt: int | None = None) -> dict:
    event = {"event": "progress", "stage": stage, "detail": detail, "max_attempts": MAX_ATTEMPTS}
    if attempt is not None:
        event["attempt"] = attempt
    return event


def run_pipeline_events(req: PlanRequest) -> Iterator[dict]:
    """파이프라인을 돌리며 진행 이벤트를 낳는 제너레이터 — 마지막 이벤트가 결과다.

    서버에 작업 상태를 저장하지 않는다(무상태 유지). 진행 상태는 이 제너레이터를
    소비하는 HTTP 연결 안에만 산다. /plan은 결과만, /plan/stream은 과정까지 흘린다.
    """
    foods = load_foods()
    matched = select_candidates(req, foods)
    # v2.0: 조건으로 거른 뒤 **요청으로 한 번 더 좁힌다.** 프롬프트에 들어가는
    # 것은 조건 통과 전부가 아니라 요청에 가까운 K건이다
    candidates, why = narrow(matched, req.request, req=req)
    yield _progress("candidates",
                    f"후보 선별 — {len(foods)}종 중 조건 통과 {len(matched)}종, "
                    f"프롬프트에 {len(candidates)}종 ({why})")

    history: list[AttemptRecord] = []
    violations: list[Violation] = []
    llm_calls: list[LlmCall] = []  # 호출별 입력·출력 전문 — 응답에 실리는 관측 데이터
    plan = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        recorded_before = len(llm_calls)
        action = "위반을 반영해 재계획" if violations else "식단 초안 생성"
        yield _progress("planner", f"계획자 호출 — {action} (시도 {attempt}/{MAX_ATTEMPTS})", attempt)
        plan = plan_meals(req, candidates, violations=violations or None, previous=plan, recorder=llm_calls)

        yield _progress("validator", "검증자 호출 — 다른 회사 모델이 원본 수치와 대조 중", attempt)
        report = merge_reports(validate_plan(req, plan, recorder=llm_calls), crosscheck_plan(req, plan, foods))
        history.append(AttemptRecord(attempt=attempt, passed=report.passed, violations=report.violations))
        for call in llm_calls[recorded_before:]:  # 이번 시도에서 나간 호출들에 시도 번호를 새긴다
            call.attempt = attempt

        if report.passed:
            yield _progress("passed", f"검증 통과 (시도 {attempt}회)", attempt)
            break
        log.info("LOOP ─ attempt %d/%d 미통과 (위반 %d건) — 위반 목록을 계획자에 회신", attempt, MAX_ATTEMPTS, len(report.violations))
        yield {
            **_progress("violations", f"위반 {len(report.violations)}건 발견 — 계획자에 회신", attempt),
            "violations": [v.model_dump() for v in report.violations],
        }
        violations = report.violations

    yield {
        "event": "result",
        "data": PlanResponse(
            meals=plan.meals,
            report=report,
            attempts=len(history),
            history=history,
            audit=audit_plan(req, plan, foods),  # "통과"의 산수 근거 — 최종 식단의 기준별 판정표
            llm_calls=llm_calls,
        ),
    }


def run_pipeline(req: PlanRequest) -> PlanResponse:
    """스트리밍 없이 결과만 — 이벤트를 소비하고 마지막 결과를 돌려준다."""
    result = None
    for event in run_pipeline_events(req):
        if event["event"] == "result":
            result = event["data"]
    return result
