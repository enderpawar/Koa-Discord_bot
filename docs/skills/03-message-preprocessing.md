# Skill 03 — Message Preprocessing

## Purpose
Discord 메시지를 TTS가 자연스럽게 읽을 수 있는 한국어 텍스트로 정제한다. 순수 함수로 단위 테스트 가능해야 한다.

## API
```python
def clean_message(message: discord.Message, max_len: int = 200) -> str:
    """TTS로 읽을 정제된 텍스트. 빈 결과면 ''."""
```

## 처리 순서 (순서 중요)
1. **봇 필터** — 호출 측에서 `message.author.bot` 사전 차단(여기서는 가정)
2. **`message.clean_content` 사용**: discord.py가 멘션 → `@닉네임`, 채널 → `#이름`, 역할 → `@역할`로 자동 치환
3. **URL 치환**: `re.sub(r"https?://\S+", "링크", text)`
4. **커스텀 이모지 제거**: `<a?:[A-Za-z0-9_]+:\d+>` → `""` (또는 이모지 이름만 남기기)
5. **마크다운 노이즈 제거**: `**`, `*`, `__`, `_`, `~~`, 백틱 → 공백
6. **연속 공백 정규화**: `re.sub(r"\s+", " ", text).strip()`
7. **길이 제한**: 200자 초과 시 슬라이스 후 `…` 부착

## Implementation Sketch
```python
import re

_URL_RE = re.compile(r"https?://\S+")
_CUSTOM_EMOJI_RE = re.compile(r"<a?:([A-Za-z0-9_]+):\d+>")
_MD_RE = re.compile(r"(\*{1,3}|_{1,3}|~{2}|`+)")
_WS_RE = re.compile(r"\s+")

def clean_message(message, max_len: int = 200) -> str:
    text = message.clean_content
    text = _URL_RE.sub("링크", text)
    text = _CUSTOM_EMOJI_RE.sub(r"\1", text)
    text = _MD_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "…"
    return text
```

## Applied Rules
- [01-bot-loop-prevention](../rules/01-bot-loop-prevention.md): 호출 측에서 봇 메시지를 사전 차단해야 함
- [07-korean-text](../rules/07-korean-text.md): 한국어 닉네임/조사가 그대로 보존되어야 함

## Dependencies
- `discord.py`의 `Message.clean_content`

## Validation
```python
# 테스트 케이스
"안녕 https://x.com 봐"        → "안녕 링크 봐"
"<@!123> ㅎㅇ"                  → "@닉네임 ㅎㅇ"   (clean_content가 처리)
"**굵게** 안녕"                  → "굵게 안녕"
"a" * 250                       → "a" * 200 + "…"
"   "                           → ""
"<:smile:1234>"                 → "smile"
```
모두 `unittest.TestCase`로 작성, Phase 3 완료 기준.
