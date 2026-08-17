# Koa 소개 홈페이지

빌드 없이 바로 열 수 있는 정적 소개 및 사용 안내 사이트입니다.

```powershell
cd site
python -m http.server 4173
```

브라우저에서 `http://127.0.0.1:4173`을 엽니다.

## 구성

- `index.html`: 문서형 정보 구조와 실제 Koa 기능 안내
- `styles.css`: 3열 데스크톱 문서 레이아웃, 모바일 단일 열, 로컬 폰트, 라이트·다크 테마
- `app.js`: KOA 인트로, 명령어 검색, 현재 섹션 표시, 모바일 메뉴
- `assets/koa-brand-banner.webp`: 원본 Banner를 웹용으로 최적화한 이미지
- `assets/koa-brand-profile.webp`: 원본 Profile을 웹용으로 최적화한 이미지
- `assets/koa-join.gif`: 하단 봇 초대 안내에 사용하는 기존 Koa GIF

GSAP은 KOA 인트로에만 사용하며 Phosphor Icons와 함께 버전이 고정된 CDN 주소로 불러옵니다.
스크롤 연출과 자동 재생 영상은 사용하지 않습니다. 모션 감소 설정에서는 인트로도 즉시 건너뜁니다.
