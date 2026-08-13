"""foods.json의 500종에 **설명문**을 붙인다 — 위키백과에서, 지어내지 않고.

왜 필요한가. 지금 계획자가 보는 후보 한 줄은 이렇다.

    [D101-004310000-0001 국밥_순대국밥 75kcal/100g 단백질3.17g 나트륨126.0mg 1인분900.0g]

숫자만 있고 **뜻이 없다.** "국물 요리 위주로", "속 편한 걸로" 같은 요청은 이
줄들로는 걸러낼 수가 없어서, 지금은 조건에 맞는 후보 210건을 통째로 프롬프트에
붓고 판단을 전부 모델에게 떠넘긴다. 설명문이 있으면 그 판단의 앞단을 검색이
맡을 수 있다. 그것이 4주차에서 배운 RAG이고, v2.0이 하려는 일이다.

**세 가지를 지킨다.**

① 세부명으로는 절대 안 찾는다. `매운탕_대구`의 `대구`를 그대로 물으면 위키백과는
   **대구광역시**를 준다. `참치구이_머리`는 신체 부위가, `물회_오징어`는 연체동물
   문서가 온다. 88%를 채울 수는 있지만 그중 몇은 확신에 찬 거짓말이다.
   그래서 질의는 **전체 이름 → 대표명** 둘뿐이고, 세부명 단독은 쓰지 않는다.

② 같은 질의는 한 번만 묻는다. 500종의 대표명은 357개다 (볶음밥만 17종). 3주차의
   `nunique()` 사다리가 여기서도 그대로 나온다. **세고 → 접고 → 남은 것만 부른다.**

③ 출처를 값 옆에 적는다. 위키백과가 준 문장과 LLM이 쓴 문장은 신뢰도가 다르고,
   섞어 두면 나중에 구분할 방법이 없다.

**그리고 여기서 4주차의 비용 사다리가 한 번 더 나온다.** 위키백과는 공짜지만
고르게 채워 주지 않는다. 밥류 90%·면류 97%인데 볶음류는 25%, 조림류는 30%다.
합성어로 된 반찬 이름에는 문서가 없기 때문이다. 그래서 순서가 이렇다.

    ① 세기      500종 → 질의 357개 (볶음밥 17종은 한 번만 묻는다)
    ② 공짜 소스  위키백과 REST API → 287종
    ③ 잔여만 LLM  남은 213종만 (--llm-fallback)

②를 건너뛰고 전부 LLM에 맡기면 500번을 부른다. ②가 먼저 오면 213번이다.
**싼 층에서 최대한 좁히고, 비싼 층은 좁혀진 것에만 쓴다.**

사용법:
  docker compose run --rm enrich-foods                  # ①②만 (공짜)
  docker compose run --rm enrich-foods --llm-fallback   # ③까지
  docker compose run --rm enrich-foods --limit 20       # 맛보기
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://ko.wikipedia.org/api/rest_v1/page/summary/"

# 위키백과 정책상 연락처가 든 User-Agent를 요구한다. **ASCII만 넣는다** —
# urllib은 헤더를 latin-1로 인코딩해서, 한글 한 글자에 요청이 통째로 죽는다.
# 이걸로 한 번 데었다. 전부 실패했는데 원인이 네트워크처럼 보였다
USER_AGENT = "week03-meal-planner/2.0 (educational; https://github.com/2026-ai-service-engineering-a)"

MAX_CHARS = 500
DATA = Path("data")
CACHE_PATH = DATA / ".wiki_cache.json"

# 문장 끝에서 자르기 위한 경계
SENTENCE_END = re.compile(r"(?<=[.!?。])\s+|(?<=다\.)\s*")


def queries(food_name: str) -> list[str]:
    """무엇을 물어볼 것인가. **세부명 단독은 목록에 없다.**

      국밥_순대국밥 → ['국밥순대국밥', '국밥']
      매운탕_대구   → ['매운탕대구', '매운탕']      ('대구'는 안 묻는다)
      부대찌개      → ['부대찌개']
    """
    base = food_name.split("(")[0]
    rep = base.split("_")[0]
    joined = base.replace("_", "")
    return list(dict.fromkeys(q for q in (joined, rep) if q))


def trim(text: str, limit: int = MAX_CHARS) -> str:
    """500자 이내로. 말이 잘리지 않게 문장 경계에서 끊는다."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    parts = SENTENCE_END.split(cut)
    if len(parts) > 1:
        joined = "".join(parts[:-1]).strip()
        if len(joined) >= limit * 0.5:
            return joined
    return cut.rstrip() + "…"


def fetch(title: str) -> str | None:
    """위키백과 요약 한 건. 없거나 동음이의 문서면 None."""
    url = API + urllib.parse.quote(title, safe="")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            payload = json.load(res)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    # 동음이의 문서는 "이 이름을 가진 것들 목록"이라 설명이 아니다
    if payload.get("type", "").endswith("disambiguation"):
        return None
    return (payload.get("extract") or "").strip() or None


LLM_SYSTEM = """너는 한국 음식 사전을 쓴다. 주어진 음식 각각에 대해 두 문장 이내,
150자 이내의 설명을 쓴다.

- 어떤 음식인지, 주재료와 조리법, 국물이 있는지 매운지 같은 성격을 적는다.
- **숫자는 쓰지 마라.** 열량·나트륨·중량은 다른 데이터가 갖고 있다.
- 모르는 음식이면 이름에서 알 수 있는 것만 적고 지어내지 마라.
- 분류를 참고하되 분류명을 그대로 옮기지는 마라."""


def fill_with_llm(missing: list[dict], docs: dict, batch_size: int) -> int:
    """위키백과가 못 채운 것만 LLM에게. **잔여만 부르는 것이 요점이다.**"""
    import instructor
    import litellm
    from pydantic import BaseModel, Field

    class Description(BaseModel):
        food_name: str
        text: str = Field(max_length=MAX_CHARS)

    class Descriptions(BaseModel):
        items: list[Description]

    client = instructor.from_litellm(litellm.completion)
    model = os.environ.get("PLANNER_MODEL", "openai/gpt-5-mini")
    filled = 0
    for i in range(0, len(missing), batch_size):
        chunk = missing[i:i + batch_size]
        listing = "\n".join(f"- {f['food_name']} ({f['category']})" for f in chunk)
        result = client.chat.completions.create(
            model=model,
            response_model=Descriptions,
            messages=[{"role": "system", "content": LLM_SYSTEM},
                      {"role": "user", "content": f"다음 음식들을 설명해라.\n{listing}"}],
        )
        by_name = {d.food_name: d.text for d in result.items}
        for food in chunk:
            text = by_name.get(food["food_name"])
            if text:
                docs[food["food_code"]] = {"source": "llm", "query": food["food_name"],
                                           "text": trim(text)}
                filled += 1
        print(f"[llm  ] {min(i + batch_size, len(missing))}/{len(missing)} "
              f"· 채운 것 {filled}건", flush=True)
    return filled


def main() -> int:
    ap = argparse.ArgumentParser(description="foods.json에 위키백과 설명문을 붙인다")
    ap.add_argument("--foods", default="data/foods.json")
    ap.add_argument("--output", default="data/food_docs.json")
    ap.add_argument("--limit", type=int, default=None, help="앞의 N종만 (맛보기)")
    ap.add_argument("--sleep", type=float, default=0.1, help="요청 간 간격(초)")
    ap.add_argument("--llm-fallback", action="store_true",
                    help="위키백과에 없는 음식만 LLM에게 설명을 받는다")
    ap.add_argument("--llm-batch", type=int, default=20, help="LLM 한 번에 물을 음식 수")
    args = ap.parse_args()

    snapshot = json.loads(Path(args.foods).read_text(encoding="utf-8"))
    foods = snapshot["foods"][: args.limit]

    # 캐시가 곧 재개 지점이다. 중간에 끊겨도 이미 물어본 것은 다시 안 묻는다
    cache: dict[str, str | None] = {}
    if CACHE_PATH.exists():
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))

    plan = {food["food_code"]: queries(food["food_name"]) for food in foods}
    unique = list(dict.fromkeys(q for qs in plan.values() for q in qs))
    print(f"[count] 음식 {len(foods)}종 → 질의 후보 {len(unique)}개 "
          f"(캐시에 {sum(1 for q in unique if q in cache)}개 있음)")

    asked = 0
    for i, query in enumerate(unique, 1):
        if query in cache:
            continue
        cache[query] = fetch(query)
        asked += 1
        if asked % 25 == 0:
            CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            print(f"[fetch] {i}/{len(unique)} · 새로 물어본 것 {asked}건", flush=True)
        time.sleep(args.sleep)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    docs, missing, by_rep = {}, [], 0
    for food in foods:
        code, name = food["food_code"], food["food_name"]
        for rank, query in enumerate(plan[code]):
            text = cache.get(query)
            if text:
                docs[code] = {"source": "wikipedia", "query": query, "text": trim(text)}
                by_rep += rank > 0  # 전체 이름이 아니라 대표명으로 맞은 것
                break
        else:
            missing.append(food)

    wiki_n = len(docs)
    llm_n = 0
    if args.llm_fallback and missing:
        print(f"[llm  ] 위키백과에 없는 {len(missing)}종만 LLM에게 묻습니다 "
              f"(전부 맡겼다면 {len(foods)}종)")
        llm_n = fill_with_llm(missing, docs, args.llm_batch)

    out = {
        "sources": {
            "wikipedia": "ko.wikipedia.org REST summary API",
            "llm": os.environ.get("PLANNER_MODEL", "openai/gpt-5-mini") if llm_n else None,
        },
        "note": "출처를 값 옆에 적는다. 위키백과 문장과 LLM이 쓴 문장은 신뢰도가 다르다.",
        "max_chars": MAX_CHARS,
        "food_count": len(foods),
        "doc_count": len(docs),
        "by_source": {"wikipedia": wiki_n, "llm": llm_n},
        "docs": docs,
    }
    Path(args.output).write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    lengths = [len(d["text"]) for d in docs.values()]
    still = [f["food_name"] for f in missing if f["food_code"] not in docs]
    print(f"[fetch] 새로 물어본 것 {asked}건 (나머지는 캐시)")
    print(f"[wiki ] {wiki_n}/{len(foods)}종 ({wiki_n / len(foods) * 100:.1f}%) "
          f"— 전체 이름으로 {wiki_n - by_rep}, 대표명으로 {by_rep}")
    if llm_n:
        print(f"[llm  ] {llm_n}종 보충 → 합계 {len(docs)}/{len(foods)}종 "
              f"({len(docs) / len(foods) * 100:.1f}%)")
    if still:
        print(f"[miss ] {len(still)}종은 비워 둡니다: "
              f"{', '.join(still[:5])}{' …' if len(still) > 5 else ''}")
    if lengths:
        print(f"[len  ] 평균 {sum(lengths) / len(lengths):.0f}자 · "
              f"최대 {max(lengths)}자 · 합계 {sum(lengths):,}자")
    print(f"[done ] → {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
