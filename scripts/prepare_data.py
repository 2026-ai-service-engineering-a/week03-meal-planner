"""K-FIND 음식 DB 엑셀 → data/foods.json 경량 스냅샷.

수집 경로 (Open-API 제한과 무관한 파일 다운로드 방식):
  1. https://various.foodsafetykorea.go.kr/nutrient/general/down/historyList.do
  2. "음식 DB" 엑셀 다운로드 (약 11.35MB, 2만 건 규모)
     — 같은 페이지의 가공식품 DB(187MB, 31만 건)는 4주차의 주인공이다
  3. uv run --with pandas --with openpyxl python scripts/prepare_data.py <음식DB.xlsx>

하는 일:
  - 데이터구분코드 = D (음식) 행만 필터
  - 140+ 컬럼 중 10개만 추출: 코드·이름·분류·에너지·탄단지·나트륨·당류·1인분량
  - 영양값은 100g 기준 그대로 두고, 1인분량(g)을 함께 실어 환산은 소비자 코드가 한다
    (1인분량이 이 데이터의 함정이자 보석 — 김밥 230g, 순대국밥 900g)
  - 시연 규모(수백 종)로 잘라 data/foods.json 저장

실데이터의 첫 교훈: 데이터 소스는 죽는다. 스냅샷과 출처 기록이 엔지니어링이다.
원본을 내려받아 재생성하면 다운로드 시점의 DB 버전·일자를 README에 기록할 것.
"""

import json
import re
import sys
from pathlib import Path

import pandas as pd

# DB 버전에 따라 컬럼명이 다를 수 있어 후보를 순서대로 찾는다
COLUMN_CANDIDATES = {
    "food_code": ["식품코드"],
    "food_name": ["식품명"],
    "category": ["식품대분류명", "식품대분류"],
    "energy_kcal_100g": ["에너지(kcal)", "에너지(㎉)"],
    "carb_g_100g": ["탄수화물(g)"],
    "protein_g_100g": ["단백질(g)"],
    "fat_g_100g": ["지방(g)"],
    "sodium_mg_100g": ["나트륨(mg)", "나트륨(㎎)"],
    "sugar_g_100g": ["당류(g)"],
    "serving_raw": ["식품중량", "1회 제공량", "1인분량"],
}
DATA_KIND_COLUMNS = ["데이터구분코드", "데이터구분"]
MAX_FOODS = 500  # 시연 규모 — 프롬프트에 통째로 들어가도 부담 없는 크기


def pick_column(df: pd.DataFrame, candidates: list[str], key: str) -> str:
    for name in candidates:
        if name in df.columns:
            return name
    raise SystemExit(f"컬럼을 찾지 못했습니다: {key} (후보: {candidates}) — DB 버전 확인 필요")


def parse_serving_g(raw) -> int | None:
    """'900g' · '900ml' · '900' 꼴에서 그램 수치만 뽑는다."""
    if pd.isna(raw):
        return None
    match = re.search(r"([\d.]+)", str(raw))
    return int(float(match.group(1))) if match else None


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"사용법: python {sys.argv[0]} <음식DB.xlsx>")

    df = pd.read_excel(sys.argv[1])

    # 음식(D) 행만 — 같은 파일 규격에 가공식품(P) 등이 섞인 버전이 있다
    for kind_col in DATA_KIND_COLUMNS:
        if kind_col in df.columns:
            df = df[df[kind_col].astype(str).str.startswith("D")]
            break

    columns = {key: pick_column(df, cands, key) for key, cands in COLUMN_CANDIDATES.items()}
    slim = df[[columns[k] for k in columns]].copy()
    slim.columns = list(columns)

    slim["serving_g"] = slim.pop("serving_raw").map(parse_serving_g)
    numeric = [c for c in slim.columns if c.endswith(("_100g", "serving_g"))]
    slim[numeric] = slim[numeric].apply(pd.to_numeric, errors="coerce")

    # 핵심 수치가 빈 행은 스냅샷에서 제외 — 환산·검증이 불가능하다
    slim = slim.dropna(subset=["energy_kcal_100g", "sodium_mg_100g", "protein_g_100g", "serving_g"])
    slim = slim.head(MAX_FOODS)

    out = Path(__file__).resolve().parents[1] / "data" / "foods.json"
    snapshot = {
        "source": "식품의약품안전처 식품영양성분 데이터베이스 — 음식 DB (파일 다운로드)",
        "source_url": "https://various.foodsafetykorea.go.kr/nutrient/general/down/historyList.do",
        "note": "영양값은 100g 기준, serving_g(1인분량)로 환산해 사용한다. 다운로드 시점의 DB 버전·일자를 README에 기록할 것.",
        "nutrition_basis": "100g",
        "food_count": len(slim),
        "foods": slim.to_dict(orient="records"),
    }
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(slim)}종 → {out}")


if __name__ == "__main__":
    main()
