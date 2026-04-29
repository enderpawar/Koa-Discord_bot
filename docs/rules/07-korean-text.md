# Rule 07 — Korean Text Handling

## Rule
**한국어 입력·닉네임·고정 안내 문구가 자연스럽게 합성되도록 보이스·인코딩·문자열 조립 규칙을 일관되게 유지한다.**

## Why
- 봇의 일차 사용자가 한국어 사용자
- 잘못된 인코딩(`encoding="utf-8"` 누락)으로 한글이 깨지면 합성 실패 또는 이상한 발음
- 영문 보이스로 한글 합성 시 발음이 부정확하거나 합성 자체가 실패
- "{user}님 입장" 같은 안내 문구의 조사 처리

## How to Apply

### 1. 보이스 고정
- 기본 보이스: `ko-KR-SunHiNeural`
- 옵션도 모두 `ko-KR-*` (영문/일문 보이스 선택 차단)
- `setvoice` choices를 한정적으로 제공

### 2. 파일 입출력 인코딩
```python
# config.json 읽기/쓰기에 인코딩 명시
path.read_text(encoding="utf-8")
json.dump(data, f, ensure_ascii=False, indent=2)   # ensure_ascii=False 중요
```
- `ensure_ascii=False`로 한글 그대로 저장 (디버깅도 용이)

### 3. 안내 문구 (한국어)
| 상황 | 문구 |
|------|------|
| 입장 | `f"{member.display_name}님 입장"` |
| 퇴장 | `f"{member.display_name}님 퇴장"` |
| 이동 | (현재 범위 외; 입장+퇴장으로 처리됨) |
| URL 치환 | `"링크"` |
| 200자 truncate | 끝에 `…` (3-dot 단일 문자 U+2026) |

### 4. 닉네임 우선순위
- `member.display_name` 사용 (서버 nickname > global name > username)
- 모든 노티 알림이 서버 닉네임을 따름

### 5. 조사 처리
- 현재는 단순화: 모든 닉네임에 "님 입장/퇴장" 사용
- "님" 자체가 받침을 무관하게 자연스러움 → 별도 조사 분기 불필요
- 향후 "이/가", "은/는" 분기가 필요하면 별도 helper

### 6. 텍스트 정규화
- `clean_message`에서 NFC 정규화는 현재 생략 (discord 입력은 일반적으로 NFC)
- 필요 시 `unicodedata.normalize("NFC", text)`

## Counter-examples
```python
# ❌ 영문 보이스로 한글 합성
voice = "en-US-AriaNeural"

# ❌ ensure_ascii가 default(True)면 한글이 \uXXXX로 저장
json.dump(data, f)

# ❌ 인코딩 누락 → Windows에서 cp949로 오해
open("config.json", "w").write(json.dumps(data))
```

## 검증
- "안녕하세요 반갑습니다" → 자연스러운 한국어 음성
- 닉네임 "김민수" → "김민수님 입장" 정확 발음
- config.json을 텍스트 에디터로 열어보면 한글 그대로 표시
