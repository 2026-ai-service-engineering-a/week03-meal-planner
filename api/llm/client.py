"""LiteLLM × instructor 래퍼 — 모든 LLM 호출이 지나는 단 한 곳.

어떤 모델을 부를지는 코드가 아니라 환경변수가 정한다 (env가 곧 조직도):
  PLANNER_MODEL · VALIDATOR_MODEL — 역할별 기본 모델
  <ROLE>_FALLBACKS               — 실패 시 시도할 모델들 (쉼표 구분, 앞에서부터)
LiteLLM이 모델 문자열의 접두사(openai/ · anthropic/ · gemini/)를 보고 알맞은
키로 호출하므로, 위층(pipeline)은 프로바이더도 폴백 순서도 알지 못한다.

실패의 층이 다르면 안전망도 다르다:
  모델 층 (429·5xx·타임아웃·없는 모델) → 폴백 체인: 다음 프로바이더로 교체
  형식 층 (필드 누락·타입 불일치)       → instructor 재시도: 검증 오류를 담아 같은 모델에 재요청

LOG_LEVEL=debug면 조립된 프롬프트 전문·모델 원출력·재시도 과정이 전부 로그에
남는다. 관측 가능성도 요구사항이다.
"""

import logging
import os
import sys

import instructor
import litellm
from dotenv import load_dotenv
from pydantic import BaseModel

# 호스트에서 직접 실행할 때 .env를 읽는다 (컨테이너는 compose의 env_file로 주입)
load_dotenv()

MAX_SCHEMA_RETRIES = 2  # 형식 층 재시도 상한 — instructor가 오류 내용을 담아 재요청

log = logging.getLogger("mealplanner.llm")
if not log.handlers:  # docker 로그(stdout)에 확실히 찍히도록 핸들러를 붙인다
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    log.addHandler(_handler)
    log.setLevel(logging.DEBUG if os.environ.get("LOG_LEVEL", "").lower() == "debug" else logging.INFO)
    log.propagate = False

_client = instructor.from_litellm(litellm.completion)

# 훅이 원출력을 역할명으로 라벨링할 수 있게, 진행 중인 호출의 태그를 기억한다
_state = {"tag": "LLM", "retries": 0}


def _raw_text(response) -> str:
    """모델 원출력 — structured output은 내부적으로 tool call JSON으로 온다."""
    try:
        message = response.choices[0].message
        if getattr(message, "tool_calls", None):
            return message.tool_calls[0].function.arguments
        return message.content or ""
    except Exception:
        return str(response)


def _on_response(response, *args, **kwargs) -> None:
    log.debug("%s RAW OUTPUT: %s", _state["tag"], _raw_text(response))


def _on_parse_error(error, *args, **kwargs) -> None:
    _state["retries"] += 1
    log.debug(
        "INSTRUCTOR RETRY %d/%d ─ validation error: %s\n(오류 내용을 담아 재요청)",
        _state["retries"],
        MAX_SCHEMA_RETRIES,
        error,
    )


try:  # 훅은 관측용 — 버전에 따라 없어도 호출 자체는 동작해야 한다
    _client.on("completion:response", _on_response)
    _client.on("parse:error", _on_parse_error)
except Exception as exc:  # pragma: no cover
    log.warning("instructor 훅 등록 실패 (관측 로그 축소): %s", exc)


def model_chain(role: str) -> list[str]:
    """[기본, 폴백…] 순서. 중복은 순서를 지키며 제거한다."""
    prefix = role.upper()
    chain = [os.environ[f"{prefix}_MODEL"]]
    for model in os.environ.get(f"{prefix}_FALLBACKS", "").split(","):
        model = model.strip()
        if model and model not in chain:
            chain.append(model)
    return chain


def _log_usage(role: str, model: str, response) -> None:
    """매 호출의 모델·토큰·비용을 남긴다 — 관측이 없으면 비용은 월말 청구서로 배운다."""
    usage = getattr(response, "usage", None)
    try:
        cost = litellm.completion_cost(completion_response=response)
    except Exception:
        cost = None  # 비용표에 없는 모델이면 토큰만 남긴다
    log.info(
        "LLM CALL ─ role=%s model=%s prompt_tokens=%s completion_tokens=%s cost=%s",
        role,
        model,
        getattr(usage, "prompt_tokens", "?"),
        getattr(usage, "completion_tokens", "?"),
        f"${cost:.6f}" if cost is not None else "?",
    )


def structured_complete[T: BaseModel](
    role: str,
    response_model: type[T],
    messages: list[dict],
    label: str = "",
) -> T:
    """역할의 모델 체인으로 structured output을 받는다. 프로바이더가 죽으면 폴백."""
    prompt_tag = f"{role.upper()} PROMPT{f' {label}' if label else ''}"
    for message in messages:
        log.debug("%s ─ %s:\n%s", prompt_tag, message["role"], message["content"])

    _state["tag"] = role.upper()
    _state["retries"] = 0

    last_error: Exception | None = None
    for i, model in enumerate(model_chain(role)):
        if i > 0:
            log.info("FALLBACK ─ %s → %s  (%s_FALLBACKS[%d])", role, model, role.upper(), i - 1)
        try:
            result, completion = _client.chat.completions.create_with_completion(
                model=model,
                response_model=response_model,
                messages=messages,
                max_retries=MAX_SCHEMA_RETRIES,
            )
            _log_usage(role, model, completion)
            return result
        except Exception as exc:  # 이 프로바이더가 죽으면 다음 폴백으로
            log.warning("LLM CALL FAILED ─ role=%s model=%s (%s)", role, model, type(exc).__name__)
            last_error = exc
    raise last_error  # 체인을 다 쓰고도 실패 — 정직한 에러
