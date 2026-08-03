"""API 스모크 테스트 — LLM 키 없이도 도는 안전망."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_plan_mock_message():
    res = client.post("/plan", json={})
    assert res.status_code == 200
    assert "영양사" in res.json()["message"]


def test_wrong_type_is_rejected_before_llm():
    """관문 ②: 틀린 입력은 LLM 요금이 나가기 전에 Pydantic이 공짜로 막는다."""
    res = client.post("/plan", json={"kcal_max": "많이"})
    assert res.status_code == 422
