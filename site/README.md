# 코아 소개 페이지

별도 빌드 도구가 필요 없는 정적 페이지입니다.

```powershell
cd site
python -m http.server 4173
```

브라우저에서 `http://127.0.0.1:4173`을 엽니다.

## 파일 구성

- `index.html`: 페이지 구조와 실제 봇 기능 카피
- `styles.css`: 반응형 레이아웃, 밝은 모드와 어두운 모드, 모션 감소 대응
- `app.js`: 명령어 탐색, 복사 상태, 색상 모드 전환, 스크롤 등장 효과
- `assets/koa-bot-banner.png`: 첫 화면 배경과 상세 이미지에 사용한 배너
