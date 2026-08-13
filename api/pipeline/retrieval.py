"""의미 검색 — 요청 한 줄로 후보를 좁힌다. **메모리에서, DB 없이.**

v1.5의 문제는 이랬다. 조건에 맞는 후보 210건을 통째로 프롬프트에 붓고,
"국물 요리 위주로" 같은 요청의 판단까지 전부 모델에게 맡겼다. 후보 목록만
15,059자다. 그리고 수정 루프가 돌면 그 15,059자를 매번 다시 보낸다.

v2.0은 그 앞에 검색을 하나 넣는다. 요청을 벡터로 만들어 설명문 500건과 비교하고,
가까운 것부터 K개만 프롬프트에 넣는다. **컨텍스트가 곧 비용이라, 컨텍스트를
줄이는 것이 비용을 줄이는 것이다.**

벡터 DB는 없다. 그리고 numpy도 안 쓴다. 500 × 768 내적은 38만 번의 곱셈이고,
순수 파이썬으로도 한 자릿수 밀리초다. api 이미지에 의존성을 하나 더 얹느니
표준 라이브러리의 `array`로 끝내는 편이 낫다.

4주차에서 Chroma를 세운 것은 31만 건이었기 때문이지 RAG라서가 아니다.
**규모가 도구를 정한다.** 그 규모가 여기서는 "라이브러리도 필요 없음"까지 내려간다.

요청이 비어 있으면 검색할 것이 없다. 그때는 분류별로 고르게 훑어 K개를 만든다.
검색이 없어도 컨텍스트는 줄일 수 있다.

**그리고 검색은 정답을 떨어뜨리면 안 된다.** 이 데이터에서 제약(500\~800kcal ·
단백질 25g 이상 · 나트륨 800mg 이하)을 전부 통과하는 음식은 500종 중 **10개**다.
유사도 상위 40건만 넘기면 그 10개 중 일부가 목록에서 빠지고, 계획자는 애초에
고를 수 없는 것들 사이에서 고르게 된다. 그래서 **제약을 통과하는 것은 유사도와
무관하게 반드시 넣는다.** 하한이 정확도보다 먼저다 — 4주차에서 HNSW의 recall을
68%에서 80%로 올린 것과 같은 이야기다.
"""

import json
import os
from array import array
from functools import lru_cache
from operator import mul
from pathlib import Path

# 프롬프트에 넣을 후보 상한. 7끼를 고르는 데 210건이 필요하지는 않다
TOP_K = int(os.environ.get("CANDIDATE_TOP_K", "40"))

MODEL = os.environ.get("EMBEDDING_MODEL", "gemini/gemini-embedding-001")
DIM = int(os.environ.get("EMBEDDING_DIM", "768"))


def _data_paths(name: str) -> list[Path]:
    override = os.environ.get("FOODS_PATH")
    roots = [Path(override).parent if override else None,
             Path("data"),
             Path(__file__).resolve().parents[2] / "data"]
    return [root / name for root in roots if root]


@lru_cache(maxsize=1)
def load_index() -> tuple[list[str], list[array], int] | None:
    """벡터 파일을 메모리에 올린다. 없으면 None — 검색 없이도 서비스는 돈다.

    1.5MB를 한 번 읽어 리스트로 쪼갠다. 500건이라 통째로 올려도 부담이 없고,
    그래서 "언제 로딩할까"를 고민할 일도 없다.
    """
    meta_path = next((p for p in _data_paths("food_vectors.json") if p.is_file()), None)
    if meta_path is None:
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    blob_path = meta_path.parent / meta["vectors"]
    if not blob_path.is_file():
        return None

    dim = meta["dim"]
    flat = array("f")
    flat.frombytes(blob_path.read_bytes())
    rows = [flat[i * dim:(i + 1) * dim] for i in range(meta["count"])]
    return meta["codes"], rows, dim


@lru_cache(maxsize=256)
def embed_query(text: str) -> tuple[float, ...]:
    """질의 임베딩. 색인은 파일로 받아 쓸 수 있지만 **질의는 매번 부른다.**

    같은 요청을 두 번 하면 캐시가 받는다. 요청 문자열은 반복되기 쉬워서
    (UI 폼의 기본값, 루프의 재시도) 이 한 줄이 실제로 호출을 줄인다.

    task_type이 색인 때와 다르다. 이 모델은 색인용과 질의용을 나눈다.
    """
    import litellm

    res = litellm.embedding(model=MODEL, input=[text], dimensions=DIM,
                            task_type="RETRIEVAL_QUERY")
    vec = res.data[0]["embedding"]
    norm = sum(x * x for x in vec) ** 0.5
    return tuple(x / norm for x in vec) if norm else tuple(vec)


def _spread(candidates: list[dict], k: int) -> list[dict]:
    """요청이 없을 때. 분류별로 돌아가며 뽑아 한쪽으로 쏠리지 않게 한다."""
    buckets: dict[str, list[dict]] = {}
    for food in candidates:
        buckets.setdefault(food["category"], []).append(food)
    picked, i = [], 0
    while len(picked) < k and any(buckets.values()):
        for foods in buckets.values():
            if i < len(foods):
                picked.append(foods[i])
                if len(picked) >= k:
                    break
        i += 1
    return picked


def feasible(food: dict, req) -> bool:
    """제약을 그대로 통과하는가. 계획자가 실제로 고를 수 있는 것들이다."""
    factor = food["serving_g"] / 100
    return (req.kcal_min <= food["energy_kcal_100g"] * factor <= req.kcal_max
            and food["protein_g_100g"] * factor >= req.protein_min_g
            and food["sodium_mg_100g"] * factor <= req.sodium_limit_mg)


def narrow(candidates: list[dict], request: str, top_k: int = TOP_K,
           req=None) -> tuple[list[dict], str]:
    """조건으로 거른 후보를 요청으로 한 번 더 좁힌다.

    반환값의 둘째는 **왜 이 목록이 나왔는지**다. 화면과 로그에 남긴다 —
    검색이 끼어든 뒤로는 "모델이 왜 저걸 골랐지"가 두 단계짜리 질문이 된다.
    """
    if len(candidates) <= top_k:
        return candidates, f"조건 통과 {len(candidates)}건 — 상한 이하라 그대로"

    if not request.strip():
        return _spread(candidates, top_k), f"요청 없음 — 분류별 고르게 {top_k}건"

    index = load_index()
    if index is None:
        return _spread(candidates, top_k), f"벡터 파일 없음 — 분류별 고르게 {top_k}건"

    codes, rows, _dim = index
    try:
        query = embed_query(request.strip())
    except Exception as exc:  # noqa: BLE001 — 키가 없거나 API가 죽어도 식단은 나와야 한다
        return _spread(candidates, top_k), f"질의 임베딩 실패({type(exc).__name__}) — 분류별 {top_k}건"

    position = {code: i for i, code in enumerate(codes)}
    pool = [(food, position[food["food_code"]]) for food in candidates
            if food["food_code"] in position]
    if not pool:
        return _spread(candidates, top_k), f"색인에 없는 후보들 — 분류별 고르게 {top_k}건"

    # 정규화된 벡터끼리의 내적이 곧 코사인이다. 500건이면 전수 비교가 그냥 된다 —
    # 근사 색인도, 벡터 DB도, numpy도 없이
    scored = sorted(
        ((sum(map(mul, rows[i], query)), food) for food, i in pool),
        key=lambda pair: -pair[0],
    )[:top_k]
    picked = [food for _, food in scored]
    note = (f"'{request.strip()}'로 {len(candidates)}건 → {len(picked)}건 "
            f"(유사도 {scored[0][0]:.3f}~{scored[-1][0]:.3f})")

    # 검색이 떨어뜨린 것 중 **제약을 통과하는 것**은 되살린다. 유사도가 낮아도
    # 계획자가 고를 수 있는 몇 안 되는 선택지라, 빠지면 목록이 통째로 무용해진다
    if req is not None:
        have = {food["food_code"] for food in picked}
        rescued = [food for food in candidates
                   if food["food_code"] not in have and feasible(food, req)]
        if rescued:
            picked.extend(rescued)
            note += f" + 제약 통과분 {len(rescued)}건 되살림"
    return picked, note
