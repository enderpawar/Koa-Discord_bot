# Skill 03 — Message Preprocessing

## Purpose
Discord 메시지를 TTS가 자연스럽게 읽을 수 있는 한국어 텍스트로 정제한다. 순수 함수로 단위 테스트 가능해야 한다.

## API
```python
def clean_message(
    message: discord.Message,
    max_len: int = 200,
    *,
    pronunciations: Mapping[str, str] | None = None,
) -> str:
    """TTS로 읽을 정제된 텍스트. 빈 결과면 ''."""


def normalize_pronunciations(rules: Mapping[str, str] | None) -> dict[str, str]:
    """저장된 원시 값을 한도 안의 검증된 사전으로 좁힌다. 어긋난 항목은 버린다."""


def detect_tone(text: str) -> str | None:
    """원문의 감정 라벨. 애매하면 None. **정제 전 원문**에 대해 부를 것."""
```

## 처리 순서 (순서 중요)
1. **봇 필터** — 호출 측에서 `message.author.bot` 사전 차단(여기서는 가정)
2. **`message.clean_content` 사용**: discord.py가 멘션 → `@닉네임`, 채널 → `#이름`, 역할 → `@역할`로 자동 치환
3. **URL 치환**: `re.sub(r"https?://\S+", "링크", text)`
4. **커스텀 이모지 제거**: `<a?:[A-Za-z0-9_]+:\d+>` → `""` (또는 이모지 이름만 남기기)
5. **마크다운 노이즈 제거**: `**`, `*`, `__`, `_`, `~~`, 백틱 → 공백
6. **서버 발음 사전 적용** — 아래 참조. 기본 자음 반응 처리보다 **먼저**여서 서버가 덮어쓸 수 있다
7. **자음 반응 정규화**: `ㅋㅎㅠㅜ` → 소리나는 대로
8. **연속 공백 정규화**: `re.sub(r"\s+", " ", text).strip()`
9. **물음표만 있는 메시지** → `으음?!` 한 번
10. **길이 제한**: 200자 초과 시 슬라이스 후 `…` 부착 (치환으로 길어진 뒤에 자른다)

## 발음 사전 (서버별)

`config.json` 의 guild 항목에 `"pronunciations": {"바꿀 말": "읽을 말"}` 로 저장한다.
편집 UI 는 관리자 대시보드(`templates/admin_dashboard.html`)에 있고, 이 모듈은
ConfigStore 를 읽지 않는다 — 호출자가 dict 를 넘겨야 순수 함수로 남는다.

계약:

- **연쇄 금지.** 규칙을 `str.replace` 로 하나씩 돌리면 `가`→`나`, `나`→`다` 를 넣었을 때
  `가` 가 `다` 로 읽힌다. 정규식 하나로 한 번만 훑어 각 위치를 정확히 한 번 바꾼다.
- **긴 원문 우선.** `롤`·`롤체` 가 함께 있으면 `롤체` 가 이긴다.
- **대소문자 무시.** `gg` 규칙이 `GG` 도 잡는다.
- **읽을 말이 비면 그 말을 지운다** (읽지 않고 건너뜀).
- **치환값은 리터럴.** `sub` 에 문자열이 아니라 함수를 넘겨 `\1` 같은 입력이 역참조로
  해석되지 않게 한다.
- 한도: 규칙 `MAX_PRONUNCIATION_RULES=100`, 원문 `MAX_PRONUNCIATION_KEY=20`자,
  읽을 말 `MAX_PRONUNCIATION_VALUE=40`자. 컴파일 결과는 `lru_cache` 로 재사용한다.
- **읽을 때는 관대하게, 쓸 때는 엄격하게.** 저장된 값의 형식 오류는
  `normalize_pronunciations` 가 조용히 버린다 (규칙 하나로 서버 TTS 전체가 멈추면 안 됨,
  Rule 03). 대시보드가 지금 보낸 값은 `web_admin_cog._pronunciation_rules` 가 400 으로
  거절해 이유를 알려 준다.

## 감정 톤

`detect_tone(text)` 이 `TONE_CHEERFUL` / `TONE_SAD` / `TONE_EXCITED` / `None` 을
돌려준다. 판정 규칙:

| 신호 | 라벨 |
|------|------|
| `ㅋㅎ` 가 `ㅠㅜ` 보다 많고 2개 이상 | `cheerful` |
| `ㅠㅜ` 가 `ㅋㅎ` 보다 많고 2개 이상 | `sad` |
| 위 신호 없이 `!` 2개 이상 | `excited` |
| 그 외 (동점 포함) | `None` |

계약:

- **정제 전 원문에 대해 부른다.** `clean_message` 가 `ㅋㅋ` → `크크` 로 바꾸므로
  순서가 뒤바뀌면 신호가 사라진다. 회귀 가드:
  `test_tone_is_read_from_raw_text_before_cleaning`.
- **애매하면 감정을 붙이지 않는다.** 단독 `ㅋ` 은 추임새/오타가 많고, `ㅋㅋㅠㅠ` 는
  어느 쪽도 아니다. 잘못 얹은 감정은 밋밋한 낭독보다 귀에 거슬린다.
- 이 모듈은 **라벨만** 정한다. 라벨 → Azure 스타일 매핑과 보이스별 지원 여부
  판단은 `tts_engine.style_for` 의 몫이다 (Skill 04).

## Implementation Sketch
```python
import re

_URL_RE = re.compile(r"https?://\S+")
_CUSTOM_EMOJI_RE = re.compile(r"<a?:([A-Za-z0-9_]+):\d+>")
_MD_RE = re.compile(r"(\*{1,3}|_{1,3}|~{2}|`+)")
_WS_RE = re.compile(r"\s+")

def clean_message(message, max_len: int = 200, *, pronunciations=None) -> str:
    text = message.clean_content
    text = _URL_RE.sub("링크", text)
    text = _CUSTOM_EMOJI_RE.sub(r"\1", text)
    text = _MD_RE.sub(" ", text)
    text = _apply_pronunciations(text, pronunciations)   # 서버 사전이 기본 규칙보다 먼저
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "…"
    return text


def _apply_pronunciations(text, rules):
    cleaned = normalize_pronunciations(rules)
    if not cleaned:
        return text
    # 규칙 전체를 패턴 하나로 묶어 한 번만 훑는다 (연쇄 치환 방지, 긴 원문 우선)
    pattern, table = _compile_pronunciations(tuple(cleaned.items()))
    return pattern.sub(lambda m: table[m.group(0).lower()], text)
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

# 발음 사전 (pronunciations=...)
"그거 ㅇㅈ"   {"ㅇㅈ": "인정"}                → "그거 인정"
"가나"        {"가": "나", "나": "다"}        → "나다"        (연쇄되지 않음)
"롤체 하자"   {"롤": "…", "롤체": "전략적 팀 전투"} → "전략적 팀 전투 하자"
"GG 치자"     {"gg": "지지"}                  → "지지 치자"   (대소문자 무시)
```
모두 `tests/unit/test_preprocess.py` 로 작성, Phase 3 완료 기준.
