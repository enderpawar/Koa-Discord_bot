# Phase 8 — Release Readiness Checklist

> 모두 통과해야 v1.0 릴리즈 가능.

## 문서
- [ ] `README.md` 존재. 다음 항목 모두 포함:
  - [ ] 봇 소개 (1, 2번 기능)
  - [ ] 설치 (`requirements.txt`, FFmpeg, Python 버전)
  - [ ] `.env` 작성 가이드
  - [ ] Discord application 생성 및 OAuth 초대 URL 가이드
  - [ ] 봇 권한 / Intents 체크리스트
  - [ ] 명령어 표
  - [ ] 트러블슈팅
- [ ] `.env.example` 제공 (실제 값 없이)
- [ ] `.gitignore`에 `.env`, `config.json`, `__pycache__/`, `*.mp3` 포함

## 코드 품질
- [ ] `print()` 사용 없음 (logging 모듈만)
- [ ] `time.sleep` 없음 (`asyncio.sleep`만)
- [ ] 모든 worker / event handler가 try/except로 감싸짐
- [ ] 모든 외부 IO에 timeout 또는 재시도 정책 명시
- [ ] config.json 쓰기는 `asyncio.Lock` 보호

## 자동 검증
- [ ] `python -m pytest tests/unit` 전부 통과 (✅ 표 확인)
- [ ] `python -m pytest tests/unit -m live` 통과 (RUN_LIVE=1)
- [ ] `.claude/scripts/check_phase_status.py` 출력에 모든 Phase가 DONE 또는 N/A

## 운영 검증
- [ ] 신규 사용자가 README만 보고 봇을 자기 서버에서 실행 성공
- [ ] 24시간 무인 운영 후 정상 동작 (메모리 leak / 좀비 task 없음)
- [ ] 봇 강제 추방 → 재초대 시 정상 동작 (config 유지)

## 릴리즈 산출물
- [ ] `requirements.txt` 정확한 버전 핀
- [ ] 라이선스 파일 (선택)
- [ ] 변경 이력(`CHANGELOG.md`) 또는 v1.0 git tag

## 결과
- 통과: ____ / 실패: ____ / 일시: ____________
