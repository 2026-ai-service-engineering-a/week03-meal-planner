"""foods.json 로딩과 후보 선별 — 모델의 상상력을 데이터의 울타리 안에 가둔다.

v1.5까지는 후보 목록을 조건만으로 걸러 통째로 프롬프트에 넣었다. 210건,
15,059자다. **v2.0은 그 뒤에 검색을 한 겹 더 둔다** (pipeline/retrieval.py).
조건은 숫자를 거르고 검색은 뜻을 거른다. 각자 아는 것만 대답하게 한다.

이 파일이 컨텍스트 엔지니어링의 첫 실물이라는 것은 그대로다. 달라진 것은
컨텍스트를 "다 넣는다"에서 "찾아서 넣는다"로 바꾼 것뿐이다.
"""

import json
import os
from functools import lru_cache
from pathlib import Path

from schemas.meal import PlanRequest


def _data_candidates(name: str) -> list[Path]:
    """컨테이너(/app/data)와 호스트(레포 루트) 어디서든 찾는다."""
    override = os.environ.get("FOODS_PATH")
    return [
        *([Path(override).parent / name] if override else []),
        Path("data") / name,
        Path(__file__).resolve().parents[2] / "data" / name,
    ]


@lru_cache(maxsize=1)
def load_foods() -> dict[str, dict]:
    """food_code → 음식 dict. 컨테이너(/app/data)와 호스트(레포 루트) 어디서든 찾는다."""
    for path in _data_candidates("foods.json"):
        if path.is_file():
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            return {food["food_code"]: food for food in snapshot["foods"]}
    raise FileNotFoundError("data/foods.json을 찾지 못했습니다 — FOODS_PATH 환경변수로 지정할 수 있습니다")


@lru_cache(maxsize=1)
def load_docs() -> dict[str, dict]:
    """food_code → 설명문. 없으면 빈 dict — 설명이 없어도 서비스는 돈다."""
    for path in _data_candidates("food_docs.json"):
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8")).get("docs", {})
    return {}


def select_candidates(req: PlanRequest, foods: dict[str, dict]) -> list[dict]:
    """조건에 맞는 후보를 거른다 — 일부러 느슨하게.

    저녁 한 끼로 말이 되는 열량대만 남기고(반찬·국 한 그릇 제외), 나트륨으로는
    거르지 않는다. 나트륨 판단을 계획자·검증자의 몫으로 남겨야 "국물 요리
    위주로" 같은 요청에서 위반과 수정 루프가 연출 없이 진짜로 돈다.
    """
    picks = []
    for food in foods.values():
        serving_kcal = food["energy_kcal_100g"] * food["serving_g"] / 100
        if req.kcal_min * 0.5 <= serving_kcal <= req.kcal_max * 1.3:
            picks.append(food)
    return sorted(picks, key=lambda food: (food["category"], food["food_name"]))
