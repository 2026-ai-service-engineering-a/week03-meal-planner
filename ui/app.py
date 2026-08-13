"""한 주 밥상 UI — 폼형 Streamlit (사전 구축 자산).

왜 채팅이 아니라 폼인가: 이 API는 무상태 단발 함수다. 채팅창을 붙이면
"이전 대화를 기억한다"는 지키지 못할 약속을 하게 된다. 서버에는 대화 상태가
없고, 상태는 아래 요청사항 폼 필드가 전부다. 서버는 매번 처음 보는 완결된
요청을 받는다.

API와의 대화는 HTTP뿐이다 — 이 파일을 Next.js로 갈아끼워도 API는 한 줄도
안 바뀐다 (헤드리스). compose 네트워크에서는 서비스 이름이 곧 호스트명이라
API_BASE_URL이 http://api:8000 이다.
"""

import json
import os

import pandas as pd
import requests
import streamlit as st

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")
SEVERITY_ICON = {"high": "🔴", "medium": "🟠", "low": "🟡"}
ROLE_KR = {"planner": "계획자", "validator": "검증자"}
ROLE_ICON = {"planner": "📝", "validator": "🔍"}
PREVIEW_CHARS = 700  # 호출 기록 미리보기 길이 — 전문은 "확대해서 보기"로

st.set_page_config(page_title="한 주 밥상", page_icon="🍚", layout="wide")


@st.cache_data(ttl=30, show_spinner=False)
def fetch_config() -> dict | None:
    """역할별 모델 구성 — UI는 env를 모르고, API(/config)가 진실의 원천이다."""
    try:
        return requests.get(f"{API_BASE_URL}/config", timeout=5).json()
    except requests.RequestException:
        return None


def call_stats(call: dict) -> str:
    """호출 1건의 토큰·비용·소요시간 한 줄."""
    parts = []
    if call.get("prompt_tokens") is not None:
        parts.append(f"입력 {call['prompt_tokens']:,} 토큰")
    if call.get("completion_tokens") is not None:
        parts.append(f"출력 {call['completion_tokens']:,} 토큰")
    if call.get("cost_usd") is not None:
        parts.append(f"${call['cost_usd']:.4f}")
    if call.get("duration_s") is not None:
        parts.append(f"{call['duration_s']:.1f}s")
    return " · ".join(parts) or "사용량 정보 없음"


def call_title(index: int, call: dict) -> str:
    role = ROLE_KR.get(call["role"], call["role"])
    icon = ROLE_ICON.get(call["role"], "🤖")
    revision = " · 재계획" if call.get("label") else ""
    return f"{icon} {index}. {role}{revision} — 시도 {call.get('attempt') or '?'} · `{call['model']}` · {call_stats(call)}"


def preview(text: str, limit: int = PREVIEW_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… (이하 생략 — 전문 {len(text):,}자는 '확대해서 보기'로)"


@st.dialog("LLM 호출 전문", width="large")
def show_call_zoom(index: int, call: dict) -> None:
    """확대해서 보기 — 입력·출력 전문을 모달로 크게 띄운다."""
    st.markdown(call_title(index, call))
    for msg in call["prompt"]:
        st.markdown(f"**입력 — {msg['role']}** ({len(msg['content']):,}자)")
        st.code(msg["content"], language=None)
    st.markdown(f"**출력 — 원문** ({len(call['raw_output']):,}자)")
    st.code(call["raw_output"], language="json")


def set_request_text(text: str) -> None:
    """suggestion 원클릭 채택·클리어 버튼이 이 콜백으로 요청사항 필드를 덮어쓴다."""
    st.session_state.request_text = text


if "request_text" not in st.session_state:
    st.session_state.request_text = ""

st.title("🍚 한 주 밥상")
st.caption(
    "나트륨 예산 안에서 저녁 7끼 — 계획자와 검증자(다른 회사 모델)가 "
    "코드의 지휘 아래 일합니다. UI는 결과를 보여줄 뿐, 두뇌는 전부 API에 있습니다."
)

# ── 조건 폼: 구조화된 조건 + 자유 요청사항 한 줄 ──────────────────────
with st.sidebar:
    st.header("조건")
    kcal_min, kcal_max = st.slider("한 끼 열량 (kcal)", 200, 1200, (500, 800), step=50)
    sodium_limit_mg = st.number_input("나트륨 한도 (mg/끼)", 200, 3000, 800, step=100)
    protein_min_g = st.number_input("단백질 최소 (g/끼)", 0, 100, 25, step=5)
    st.divider()
    config = fetch_config()
    if config:
        st.markdown("**모델 구성** — env가 곧 조직도")
        st.markdown(f"📝 계획자: `{config['planner_model'] or '(미설정)'}`")
        st.markdown(f"🔍 검증자: `{config['validator_model'] or '(미설정)'}`")
        if config["validator_fallbacks"]:
            st.markdown("↩️ 폴백: " + " → ".join(f"`{m}`" for m in config["validator_fallbacks"]))
        st.caption("두 역할은 일부러 다른 회사입니다 — 만든 쪽이 검사하면 안 되니까요. 교체는 .env 수정 + 재기동뿐.")
    st.caption(f"API: `{API_BASE_URL}`")

st.text_input(
    "요청사항 한 줄 — 비우면 조건만으로 새로 생성 (상태는 이 필드가 전부)",
    key="request_text",
    placeholder='예: "국물 요리 위주로 짜줘"',
)

col_run, col_clear = st.columns([3, 1])
run_clicked = col_run.button("식단 짜기", type="primary", use_container_width=True)
col_clear.button(
    "요청사항 클리어",
    use_container_width=True,
    on_click=set_request_text,
    args=("",),
    help="필드를 비우면 전체가 초기 상태로 돌아갑니다 — 서버에는 기억이 없습니다",
)

if run_clicked:
    payload = {
        "kcal_min": kcal_min,
        "kcal_max": kcal_max,
        "sodium_limit_mg": sodium_limit_mg,
        "protein_min_g": protein_min_g,
        "request": st.session_state.request_text,
    }
    # /plan/stream(SSE)로 중간 과정을 실시간 표시 — 서버는 여전히 무상태,
    # 진행 상태는 이 HTTP 연결 안에만 산다
    status = st.status("파이프라인 시작 — 순서·반복·종료는 코드가 쥡니다", expanded=True)
    try:
        with requests.post(f"{API_BASE_URL}/plan/stream", json=payload, stream=True, timeout=600) as res:
            res.raise_for_status()
            res.encoding = "utf-8"
            for line in res.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                event = json.loads(line[len("data: "):])
                if event["event"] == "result":
                    st.session_state.last_result = {"payload": payload, "data": event["data"]}
                    status.update(label="완료 — 아래에 결과를 그렸습니다", state="complete", expanded=False)
                    continue
                status.write(event["detail"])
                if event.get("stage") == "violations":
                    for v in event.get("violations", [])[:5]:
                        status.write(f"　↳ [{v['severity']}] {v['day']} {v['type']} — {v['evidence']}")
                status.update(label=event["detail"])
    except requests.RequestException as exc:
        status.update(label="API 호출 실패", state="error")
        st.error(f"API 호출 실패: {exc}")

# ── 결과 대시보드 ─────────────────────────────────────────────────────
result = st.session_state.get("last_result")
if not result:
    st.info("왼쪽에서 조건을 잡고 **식단 짜기**를 누르세요.")
    st.stop()

data = result["data"]

# v0.1 목 응답: 아직 계획자가 없다
if not data.get("meals"):
    st.warning(data.get("message", "응답에 식단이 없습니다"))
    st.stop()

report = data.get("report") or {}
attempts = data.get("attempts", 1)
history = data.get("history", [])

m1, m2, m3 = st.columns(3)
m1.metric("검증", "통과 ✅" if report.get("passed") else "미통과 ⚠️")
m2.metric("시도 횟수", f"{attempts}회")
m3.metric("남은 위반", f"{len(report.get('violations', []))}건")

# 후보가 어떻게 추려졌는지 — v2.0에서 검색이 끼어든 뒤로 필요해진 줄.
# "모델이 왜 저걸 골랐나"의 앞단이 여기 있다
retrieval = (data.get("audit") or {}).get("retrieval")
if retrieval:
    st.caption(
        f"후보 {retrieval['total']}종 → 조건 통과 {retrieval['matched']}종 "
        f"→ 프롬프트에 {retrieval['sent']}종 · {retrieval['why']}"
    )

# 통과가 "감상"이 아니라 무엇을 확인한 결과인지 — audit(코드 재계산 근거) 요약
audit = data.get("audit")
if audit:
    checks = audit["meals"]
    total = len(checks)

    def ok_count(key: str) -> int:
        return sum(1 for check in checks if check[key])

    mark = lambda flag: "✅" if flag else "❌"  # noqa: E731
    st.caption(
        f"확인 항목 (코드 재계산): 실존 {ok_count('exists')}/{total} · "
        f"환산 일치 {ok_count('conversion_ok')}/{total} · 열량 범위 {ok_count('kcal_ok')}/{total} · "
        f"단백질 {ok_count('protein_ok')}/{total} · 나트륨 {ok_count('sodium_ok')}/{total} · "
        f"요일 구성 {mark(audit['days_complete'])} · 대표식품 ≤2회 {mark(audit['repetition_ok'])}"
    )

# 식단표
meals = data["meals"]
df = pd.DataFrame(
    [
        {
            "요일": m["day"],
            "음식": m["food_name"],
            "1인분(g)": m["serving_g"],
            "열량(kcal)": m["calories_kcal"],
            "단백질(g)": m["protein_g"],
            "나트륨(mg)": m["sodium_mg"],
            "선정 이유": m["reason"],
        }
        for m in meals
    ]
)
st.dataframe(df, use_container_width=True, hide_index=True)
st.caption(
    f"평균 열량 {df['열량(kcal)'].mean():,.0f} kcal · "
    f"평균 나트륨 {df['나트륨(mg)'].mean():,.0f} mg · "
    f"평균 단백질 {df['단백질(g)'].mean():,.1f} g"
)

# 검증 상세 — 끼니별 기준 판정표. 수치는 API의 audit(순수 함수 재계산)를 그대로 그린다
if audit:
    with st.expander("검증 상세 — 무엇을 확인해서 나온 판정인가", expanded=not report.get("passed", False)):
        c = audit["constraints"]
        st.caption(  # 물결표(~) 두 개는 마크다운 취소선이 되므로 이스케이프한다
            f"기준: 한 끼 {c['kcal_min']}\\~{c['kcal_max']}kcal · 단백질 ≥{c['protein_min_g']}g · "
            f"나트륨 ≤{c['sodium_limit_mg']}mg · 같은 대표식품 주 2회 이하 · 월\\~일 각 1끼"
        )
        audit_df = pd.DataFrame(
            [
                {
                    "요일": check["day"],
                    "음식": check["food_name"],
                    "DB 실존": mark(check["exists"]),
                    "환산 일치": mark(check["conversion_ok"]),
                    "열량(kcal)": f"{check['kcal']:,.0f} {mark(check['kcal_ok'])}",
                    "단백질(g)": f"{check['protein_g']:,.1f} {mark(check['protein_ok'])}",
                    "나트륨(mg)": f"{check['sodium_mg']:,.0f} {mark(check['sodium_ok'])}",
                }
                for check in checks
            ]
        )
        st.dataframe(audit_df, use_container_width=True, hide_index=True)

        repeated = {rep: n for rep, n in audit["rep_counts"].items() if n > 1}
        rep_text = " · ".join(f"{rep} {n}회" for rep, n in repeated.items()) if repeated else "중복 없음"
        st.caption(
            f"요일 구성 {mark(audit['days_complete'])} (월~일 각 1끼) · "
            f"대표식품 2회 이상: {rep_text} {mark(audit['repetition_ok'])} · "
            f"주간 나트륨 합계 {audit['weekly_sodium_mg']:,.0f} mg"
        )
        st.caption(
            "수치는 전부 서버 코드가 원본 100g값 × 1인분량으로 재계산한 값입니다 — "
            "LLM 검증자의 판정과 별개로, 산수는 코드가 이중 보증합니다."
        )


def render_violations(violations: list, key_prefix: str) -> None:
    """위반 리포트 — suggestion은 원클릭으로 요청사항 필드에 채택된다."""
    for i, v in enumerate(violations):
        icon = SEVERITY_ICON.get(v.get("severity", "low"), "⚪")
        with st.container(border=True):
            st.markdown(f"{icon} **{v['type']}** · {v['day']}요일 · `{v['food_code']}`")
            st.markdown(f"근거: {v['evidence']}")
            cols = st.columns([3, 1])
            cols[0].markdown(f"제안: _{v['suggestion']}_")
            cols[1].button(
                "제안 채택 →",
                key=f"adopt_{key_prefix}_{i}",
                on_click=set_request_text,
                args=(v["suggestion"],),
                help="요청사항 필드에 넣고 '식단 짜기'를 다시 누르세요 — 서버에는 매번 새 요청입니다",
            )


if report.get("violations"):
    st.subheader("남은 위반 (정직한 실패)")
    st.caption("3회 안에 통과하지 못하면 마지막 안과 위반 목록을 함께 반환합니다.")
    render_violations(report["violations"], "final")

if history:
    with st.expander(f"수정 루프 이력 — 루프가 돌았다는 증거 ({len(history)}건)"):
        for h in history:
            state = "통과 ✅" if h.get("passed") else "미통과 ❌"
            st.markdown(f"**시도 {h['attempt']}** — {state}")
            if h.get("violations"):
                render_violations(h["violations"], f"h{h['attempt']}")

# LLM 호출 기록 — 각 요청의 입력·출력 전문 (개발 참고용, 기본 접힘)
llm_calls = data.get("llm_calls") or []
if llm_calls:
    total_in = sum(call.get("prompt_tokens") or 0 for call in llm_calls)
    total_out = sum(call.get("completion_tokens") or 0 for call in llm_calls)
    total_cost = sum(call.get("cost_usd") or 0 for call in llm_calls)
    st.subheader("LLM 호출 기록")
    st.caption(
        f"개발 참고용 — 총 {len(llm_calls)}건 · 입력 {total_in:,} 토큰 · 출력 {total_out:,} 토큰 · "
        f"약 ${total_cost:.4f}. 모델이 보는 것은 객체가 아니라 결국 이 텍스트입니다."
    )
    for i, call in enumerate(llm_calls, start=1):
        with st.expander(call_title(i, call), expanded=False):
            if st.button("🔎 확대해서 보기 — 입력·출력 전문", key=f"zoom_{i}"):
                show_call_zoom(i, call)
            for msg in call["prompt"]:
                st.markdown(f"**입력 — {msg['role']}** ({len(msg['content']):,}자)")
                st.code(preview(msg["content"]), language=None)
            st.markdown(f"**출력 — 원문** ({len(call['raw_output']):,}자)")
            st.code(preview(call["raw_output"]), language="json")
