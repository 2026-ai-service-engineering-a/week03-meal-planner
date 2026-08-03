# week03-meal-planner — 한 주 밥상 🍚

나트륨 예산 안에서 저녁 7끼를 짜는 **식단 플래너 API**. 3주차 라이브 시연
저장소입니다.

이번 주는 일부러 **에이전트가 아닌 것**을 만듭니다. 파이프라인의 순서·반복·
종료를 전부 코드가 쥐고, 모델은 각 단계의 일꾼으로 부려집니다. LLM 호출
지점은 정확히 두 곳 — **계획자**(식단 생성)와 **검증자**(위반 검사) — 이고,
둘은 일부러 **다른 회사 모델**로 교차합니다 (self-preference bias: 만든 쪽이
검사하면 안 됩니다).

```
UI (Streamlit, 폼형) ──HTTP──> API (FastAPI, 헤드리스)
                                ├─ 계획자   PLANNER_MODEL   (예: OpenAI)
                                ├─ 검증자   VALIDATOR_MODEL (예: Claude, 폴백: VALIDATOR_FALLBACKS)
                                ├─ 크로스체크 (순수 함수 — 환산 재계산)
                                └─ 수정 루프 (코드가 제어, 상한 3회)
```

## 시작하기

기본 브랜치 `main`은 완성본입니다. 그냥 클론하면 정답을 받으니, 시작점은
**v0.1 태그**에서 작업 브랜치를 잡습니다.

```bash
git clone https://github.com/2026-ai-service-engineering-a/week03-meal-planner.git
cd week03-meal-planner
git switch -c week03 v0.1   # v0.1 태그에서 작업 브랜치 생성

cp .env.example .env        # 프로바이더 키 입력 (아래 참고)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

- UI: http://localhost:8501 · API 문서(Swagger): http://localhost:8000/docs
- 완성본과 비교: `git checkout v1.0` · `git diff v0.1 v1.0`
- v0.1은 **키 없이도 뜹니다** — UI와 목 응답("아직 영양사가 출근 전입니다")까지 확인 가능

같은 API를 curl로 불러도 똑같이 동작합니다 (헤드리스 분리의 증명):

```bash
curl -s localhost:8000/plan -X POST -H 'Content-Type: application/json' \
  -d '{"kcal_min":500,"kcal_max":800,"sodium_limit_mg":800,"protein_min_g":25,"request":""}' | jq
```

## API 키 준비

계획자·검증자를 다른 회사로 교차시키는 것이 이번 주의 핵심이라, 3개 중
**2개 이상**을 권장합니다. 1개뿐이면 같은 회사의 다른 모델로 구성할 수는
있지만, 교차 검증의 의미는 줄어듭니다.

| 프로바이더 | 발급 |
| --- | --- |
| OpenAI | https://platform.openai.com/api-keys |
| Anthropic | https://console.anthropic.com/settings/keys |
| Gemini | https://aistudio.google.com/apikey (무료 발급 가능) |

역할 배정은 전부 `.env`에 있습니다 — **env가 곧 조직도**입니다. 코드 어디에도
모델명이 없어, 조합 교체는 env 수정 + `docker compose up -d --force-recreate api`가
전부입니다.

## 데이터: 진짜 정부 데이터

`data/foods.json`은 [식품안전나라 K-FIND 식품영양성분 DB](https://various.foodsafetykorea.go.kr/nutrient/general/down/historyList.do)
**음식 DB**(엑셀 파일 다운로드, 약 11.35MB·2만 건 규모)에서 10개 컬럼만 추출한
경량 스냅샷입니다 (음식 94종, 시연 규모).

- 영양값은 **100g 기준**이고 `serving_g`(1인분량)가 따로 있습니다. 사람이 먹는
  단위로 환산해야 하며, 이 환산이 LLM이 정확히 틀리는 지점입니다
  (예: 순대국밥 나트륨 470mg/100g × 1인분 900g = **4,230mg** — WHO 하루 권장의 2배)
- 원본에서 재생성: `scripts/prepare_data.py` 참고. 파일 다운로드 방식이라
  Open-API 제한과 무관합니다. **재생성 시 다운로드 시점의 DB 버전·일자를 이
  절에 기록하세요** — 데이터에도 버전이 있습니다
- 현재 스냅샷: 음식DB 규격 기반 시연용 경량판 (2026-08-03 생성)

## 저장소 구조

```
├── api/                  # 헤드리스 API (오늘 만드는 것)
│   ├── app/main.py       #   FastAPI /plan
│   └── ...               #   v1.0: schemas/ llm/ pipeline/ tests/
├── ui/app.py             # Streamlit 폼형 UI (사전 구축 — 오늘 안 건드림)
├── data/foods.json       # 식약처 음식DB 스냅샷
├── scripts/prepare_data.py
├── examples/             # 0단 나쁜 프롬프트 (의도된 실패 예제)
├── docker-compose.yml    # api + ui (DB 없음)
├── docker-compose.dev.yml
└── PROMPT.md             # 회전별 지시 프롬프트 기록
```

### UI가 폼형인 이유

UI의 형태는 백엔드 계약의 번역이어야 합니다. 이 API는 **무상태 단발 함수**라
채팅창을 붙이면 "이전 대화를 기억한다"는 지키지 못할 약속을 하게 됩니다.
폼에는 구조화된 조건(칼로리·나트륨·단백질)과 자유 요청사항 한 줄이 있고,
검증 리포트의 `suggestion`을 원클릭으로 요청사항에 채택해 재요청하면
수정처럼 느껴지지만 서버는 매번 처음 보는 완결된 요청을 받습니다.
**상태는 폼에만 있습니다.** UI는 사전 구축 자산이라 오늘의 학습 목표가
아니고, 직접 만드는 것은 9주차입니다.

## 0단 예제: 나쁜 프롬프트의 실패 관찰

```bash
docker compose exec api uv run python examples/naive_plan.py   # 실행할 때마다 형식이 다르다
docker compose exec api uv run python examples/naive_parse.py  # json.loads → 첫 글자에서 즉사
grep 연어 data/foods.json                                       # 환각 확인 — DB에 없는 음식
```

| 실패 | 처방 |
| --- | --- |
| 환각 (DB에 없는 음식) | 후보 목록 컨텍스트 주입 (1회전) |
| 형식 붕괴 (인사말 포장, 키 이름 변동) | instructor 스키마 강제 (1회전) |
| 수치 창작 ("약 200mg") | 데이터 대조·크로스체크 (2회전) |

## 테스트

```bash
docker compose exec api uv run pytest -v
```
