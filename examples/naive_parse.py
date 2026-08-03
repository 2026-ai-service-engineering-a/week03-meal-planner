"""0단 출력(인사말 포함)을 json.loads — 첫 글자에서 즉사한다.

이 에러를 try/except와 정규식으로 막기 시작하면 늪이다. 그 늪의 이름이 "JSON 흉내".
naive_plan.py 실행 결과를 파이프로 넣어도 된다:
  uv run python examples/naive_plan.py | uv run python examples/naive_parse.py
"""
import json, sys

text = sys.stdin.read() if not sys.stdin.isatty() else """물론이죠! 나트륨을 줄인 건강한 일주일 저녁 식단입니다 😊

```json
{"월요일": {"메뉴": "연어 스테이크 샐러드", "나트륨": "350mg"}}
```
맛있게 드세요!"""
print(json.loads(text))
