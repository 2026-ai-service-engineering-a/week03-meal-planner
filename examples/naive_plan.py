"""이렇게 하면 안 되는 이유를 보여주는 파일 — 지우지 말 것"""
import os, litellm

resp = litellm.completion(
    model=os.environ["PLANNER_MODEL"],
    messages=[{
        "role": "user",
        "content": "일주일 저녁 식단을 나트륨 적게 JSON으로 짜줘",
    }],
)
print(resp.choices[0].message.content)
