"""설명문 500건을 벡터로 만들어 **파일 하나**에 담는다. 벡터 DB는 쓰지 않는다.

4주차에서는 31만 건이라 Chroma 서비스를 세웠다. 여기는 500건이다. 500 × 768
float32는 1.5MB이고, 코사인 전수 비교는 numpy로 밀리초 단위다. **서비스를 하나
더 세울 이유가 없다.**

  4주차 31만 건  → 1.6GB · HNSW 근사 색인 · compose 서비스 · Google Drive 배포
  3주차 500건    → 1.5MB · 전수 비교 · 파일 하나 · git에 커밋

"없어도 되는데 왜 쓰는가"를 답할 수 있어야 도입한 것이다. 여기서는 답이 없다.
그래서 안 쓴다. **규모가 도구를 정한다.**

벡터를 저장소에 커밋하는 것도 같은 이유다. 1.5MB는 git에 들어가고, 그러면
재현자는 임베딩 키 없이도 검색을 돌려볼 수 있다. 4주차의 1.6GB는 못 들어가서
Drive로 갔다. **배포할 수 있느냐가 설계를 바꾼다.**

사용법:
  docker compose run --rm embed-foods
"""

import argparse
import json
import os
import struct
import sys
import time
from pathlib import Path

MODEL = os.environ.get("EMBEDDING_MODEL", "gemini/gemini-embedding-001")
DIM = int(os.environ.get("EMBEDDING_DIM", "768"))
BATCH = int(os.environ.get("EMBED_BATCH_SIZE", "50"))


def compose(food: dict, doc: dict | None) -> str:
    """무엇을 임베딩할 것인가.

    4주차의 A/B 실험이 내린 결론(이름만으로는 약하다, 분류를 붙여라)을 그대로
    쓰되, 여기에는 설명문이 하나 더 있다. 이름 · 분류 · 설명 순으로 잇는다.
    영양 수치는 넣지 않는다 — 숫자는 필터의 일이다.
    """
    parts = [food["food_name"].replace("_", " "), food["category"]]
    if doc and doc.get("text"):
        parts.append(doc["text"])
    return " · ".join(parts)


def normalize(vec: list[float]) -> list[float]:
    """차원을 줄인 벡터는 길이가 1이 아니다. 정규화해야 코사인이 맞는다."""
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec] if norm else vec


def embed(texts: list[str]) -> list[list[float]]:
    """색인용 임베딩. task_type이 질의용과 다르다."""
    import litellm

    res = litellm.embedding(model=MODEL, input=texts, dimensions=DIM,
                            task_type="RETRIEVAL_DOCUMENT")
    return [normalize(d["embedding"]) for d in res.data]


def main() -> int:
    ap = argparse.ArgumentParser(description="설명문 → 벡터 파일 하나")
    ap.add_argument("--foods", default="data/foods.json")
    ap.add_argument("--docs", default="data/food_docs.json")
    ap.add_argument("--output", default="data/food_vectors.bin")
    ap.add_argument("--meta", default="data/food_vectors.json")
    args = ap.parse_args()

    foods = json.loads(Path(args.foods).read_text(encoding="utf-8"))["foods"]
    docs = json.loads(Path(args.docs).read_text(encoding="utf-8"))["docs"]

    codes = [f["food_code"] for f in foods]
    texts = [compose(f, docs.get(f["food_code"])) for f in foods]
    with_doc = sum(1 for f in foods if docs.get(f["food_code"], {}).get("text"))
    print(f"[embed] {len(texts)}건 · {MODEL} · {DIM}차원 "
          f"(설명 있는 것 {with_doc}, 없는 것 {len(texts) - with_doc})")
    print(f"[embed] 예시: {texts[0][:80]}")

    t0 = time.time()
    vectors: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        vectors.extend(embed(texts[i:i + BATCH]))
        print(f"[embed] {len(vectors)}/{len(texts)}", flush=True)
    elapsed = time.time() - t0

    # float32 평문 배열. numpy 없이 쓰고 numpy로 읽는다 — 형식이 단순할수록
    # "이 파일이 뭔지" 설명할 것이 줄어든다
    blob = b"".join(struct.pack(f"<{DIM}f", *v) for v in vectors)
    Path(args.output).write_bytes(blob)
    Path(args.meta).write_text(json.dumps({
        "model": MODEL, "dim": DIM, "count": len(codes),
        "recipe": "food_name · category · description",
        "dtype": "float32", "normalized": True,
        "vectors": Path(args.output).name,
        "codes": codes,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    size_mb = len(blob) / 1024 / 1024
    print(f"[done ] {len(vectors)}건 · {elapsed:.1f}초 · {size_mb:.1f}MB → {args.output}")
    print(f"[done ] 벡터 DB 없이 파일 하나입니다. 31만 건이었다면 이야기가 달랐습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
