"""검색 층 테스트 — 키도 벡터 파일도 없이 도는 것만 여기 둔다.

CI에는 임베딩 키가 없다. 그 조건에서 확인할 수 있는 것은 **검색이 실패해도
식단은 나온다**와 **제약을 통과하는 후보는 절대 안 빠진다** 둘이고, 사실
그 둘이 이 층에서 제일 중요한 성질이다.
"""

from pipeline import retrieval
from schemas.meal import PlanRequest

REQ = PlanRequest(kcal_min=500, kcal_max=800, sodium_limit_mg=800, protein_min_g=25)


def food(code: str, name: str, category: str, kcal: float, protein: float,
         sodium: float, serving: float = 100.0) -> dict:
    return {"food_code": code, "food_name": name, "category": category,
            "energy_kcal_100g": kcal, "protein_g_100g": protein,
            "sodium_mg_100g": sodium, "serving_g": serving}


def test_small_candidate_list_passes_through():
    """상한 이하면 손대지 않는다. 500건이 아니라 30건이면 검색할 이유가 없다."""
    candidates = [food(f"C{i}", f"음식{i}", "밥류", 600, 30, 500) for i in range(10)]
    picked, why = retrieval.narrow(candidates, "국물 요리", top_k=40, req=REQ)
    assert picked == candidates
    assert "상한 이하" in why


def test_empty_request_spreads_across_categories():
    """요청이 없으면 검색할 것이 없다. 그래도 컨텍스트는 줄인다."""
    candidates = ([food(f"A{i}", f"밥{i}", "밥류", 600, 30, 500) for i in range(30)]
                  + [food(f"B{i}", f"국{i}", "국 및 탕류", 600, 30, 500) for i in range(30)])
    picked, why = retrieval.narrow(candidates, "", top_k=10, req=REQ)
    assert len(picked) == 10
    # 한쪽 분류로 쏠리지 않는다
    assert len({f["category"] for f in picked}) == 2
    assert "분류별" in why


def test_missing_vector_file_degrades_to_spread(monkeypatch):
    """벡터 파일이 없어도 500이 아니라 후보 목록이 나온다."""
    monkeypatch.setattr(retrieval, "load_index", lambda: None)
    candidates = [food(f"C{i}", f"음식{i}", "밥류", 600, 30, 500) for i in range(60)]
    picked, why = retrieval.narrow(candidates, "매콤한 것", top_k=10, req=REQ)
    assert len(picked) == 10
    assert "벡터 파일 없음" in why


def test_embedding_failure_degrades_to_spread(monkeypatch):
    """키가 없거나 API가 죽어도 식단은 나와야 한다. 검색은 부가 기능이다."""
    monkeypatch.setattr(retrieval, "load_index", lambda: (["C0"], [(1.0,)], 1))
    monkeypatch.setattr(retrieval, "embed_query",
                        lambda text: (_ for _ in ()).throw(RuntimeError("no key")))
    candidates = [food(f"C{i}", f"음식{i}", "밥류", 600, 30, 500) for i in range(60)]
    picked, why = retrieval.narrow(candidates, "매콤한 것", top_k=10, req=REQ)
    assert len(picked) == 10
    assert "임베딩 실패" in why


def test_feasible_foods_are_never_dropped(monkeypatch):
    """**검색이 정답을 떨어뜨리면 안 된다.**

    이 데이터에서 제약을 전부 통과하는 음식은 500종 중 10개다. 유사도 상위
    K건만 넘기면 그중 일부가 빠지고, 계획자는 고를 수 없는 것들 사이에서
    고르게 된다. 유사도가 꼴찌여도 제약을 통과하면 목록에 남아야 한다.
    """
    # 제약을 통과하는 것 하나(단백질 30g·나트륨 500mg)와 못 통과하는 것 여럿
    winner = food("WIN", "고등어구이", "구이류", 600, 30, 500)
    others = [food(f"X{i}", f"짠음식{i}", "국 및 탕류", 600, 30, 5000) for i in range(60)]

    # winner를 유사도 꼴찌로 만든다. 검색만 믿으면 잘리는 자리다
    pool = others + [winner]
    codes = [f["food_code"] for f in pool]
    rows = [(0.9,) for _ in others] + [(0.1,)]
    monkeypatch.setattr(retrieval, "load_index", lambda: (codes, rows, 1))
    monkeypatch.setattr(retrieval, "embed_query", lambda text: (1.0,))

    picked, why = retrieval.narrow(pool, "국물", top_k=5, req=REQ)
    assert winner in picked, "제약을 통과하는 후보가 검색에서 빠졌다"
    assert "되살림" in why


def test_feasible_matches_the_constraint_arithmetic():
    """1인분 환산으로 판정한다. 100g 기준값 그대로가 아니다."""
    # 100g당 75kcal이지만 1인분이 900g이라 675kcal
    soup = food("S", "순대국밥", "국 및 탕류", 75, 3.2, 126, serving=900)
    assert retrieval.feasible(soup, REQ) is False  # 단백질 28.8g은 넘지만 나트륨 1,134mg
    lean = food("L", "고등어구이", "구이류", 340, 23.4, 126, serving=200)
    assert retrieval.feasible(lean, REQ) is True
