# Rules Index

Rule은 본 봇 전반에 걸쳐 적용되는 **불변 제약(invariant) / 정책**입니다. Skill 구현은 자유롭게 변형되어도, Rule은 항상 충족되어야 합니다.

각 파일은 다음을 담습니다.
- **Rule**: 한 문장 규칙
- **Why**: 위반 시 어떤 사고/문제가 발생하는가
- **How to apply**: 코드/설정/리뷰에서 어떻게 적용하는가
- **Counter-examples**: 잘못된 예 → 올바른 예

| # | Rule | 한 줄 요약 |
|---|------|----------|
| 01 | [bot-loop-prevention](01-bot-loop-prevention.md) | 봇이 만든 메시지/이벤트로 봇이 다시 트리거되면 안 된다 |
| 02 | [guild-isolation](02-guild-isolation.md) | 모든 상태(설정·큐·voice client)는 guild_id로 격리한다 |
| 03 | [error-resilience](03-error-resilience.md) | 어떤 예외도 봇 프로세스를 죽여선 안 된다 |
| 04 | [secrets-and-security](04-secrets-and-security.md) | 토큰/시크릿은 `.env`에만, 명령어는 권한 체크 |
| 05 | [async-correctness](05-async-correctness.md) | blocking IO 금지, 콜백은 `asyncio.Event`로 직렬화 |
| 06 | [logging-standards](06-logging-standards.md) | `print` 금지, `logging` 모듈 + 일관된 레벨 |
| 07 | [korean-text](07-korean-text.md) | 한국어 입력/닉네임/조사가 자연스럽게 처리되어야 함 |

## 우선순위
규칙들이 충돌할 때의 순서: **01 ≥ 02 ≥ 03 ≥ 04 ≥ 05 ≥ 06 ≥ 07**
- 안전(루프 방지·격리·복원력) > 보안 > 정확성(async) > 가독성(로깅) > UX(한국어).
