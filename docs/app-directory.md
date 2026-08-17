# App Directory 등재 준비

디스코드 App Directory에 코아를 올리기 위한 요건, 남은 작업, 그리고 제출 화면에 그대로
붙여 넣을 카피를 모아 둔 문서입니다.

## 핵심 제약

**App Directory 등재는 인증된(verified) 앱만 가능합니다.** 인증은 봇이 100개 서버에 도달하면
의무가 되며, 그 전에는 서버 수가 늘기를 기다리거나 Developer Portal의 인증 신청 흐름을 직접
확인해야 합니다. 즉 등재는 "지금 서류만 채우면 되는 일"이 아니라 **인증 → 디스커버리 활성화**
두 단계를 거칩니다.

인증 여부와 무관하게 **지금 준비해 둘 수 있는 것**이 아래 항목들이고, 그중 약관·개인정보처리방침은
이미 만들어 두었습니다.

## 준비 상태

| 요건 | 상태 | 비고 |
|---|---|---|
| 공개 호스팅된 **개인정보처리방침** | ✅ 완료 | `site/privacy.html` |
| 공개 호스팅된 **이용약관** | ✅ 완료 | `site/terms.html` |
| 문의 연락 수단 | ❌ **미작성** | 두 페이지의 `[작성 필요]` 블록. 이메일 또는 지원 서버 초대 링크 |
| 앱 아이콘 | ⚠️ 확인 필요 | Developer Portal에 512×512 이상 등록 여부 |
| 앱 설명 (짧은/긴) | ✅ 카피 준비됨 | 아래 [등재 카피](#등재-카피) |
| 태그·카테고리 | ✅ 준비됨 | 아래 [태그](#태그) |
| 스크린샷 | ⚠️ 부족 | 현재 4장. 파티 모집 실제 화면이 없음 |
| 소유자 계정 2단계 인증 | ⚠️ 확인 필요 | 인증 신청의 전제 조건 |
| 연령 제한 콘텐츠 없음 | ✅ 해당 없음 | 운세는 오락용이며 성인 콘텐츠 아님 |
| 앱 인증(verified) | ❌ 미완료 | **등재의 전제 조건** |

### URL (GitHub Pages 배포 후 유효)

```text
이용약관          https://enderpawar.github.io/Koa-Discord_bot/terms.html
개인정보처리방침    https://enderpawar.github.io/Koa-Discord_bot/privacy.html
```

두 값을 Developer Portal → **General Information** 의 `Terms of Service URL` /
`Privacy Policy URL` 칸에 넣습니다.

> `site/**` 경로가 바뀌면 `deploy-pages.yml`이 자동으로 배포합니다. 위 URL이 200으로
> 응답하는지 확인한 뒤 제출하세요.

## 등재 카피

### 앱 이름

```text
코아 (Koa)
```

### 짧은 설명 — 한 줄 요약

```text
채팅을 한국어 음성으로 읽어 주고, 파티 모집과 주간 활동 순위까지 챙기는 서버 도구
```

### 긴 설명

```text
코아는 한국어 디스코드 커뮤니티를 위한 음성·서버 관리 봇입니다.

■ 채팅을 음성으로
음성 채널에 들어가 /입장 한 번이면, 그 채널의 채팅을 자연스러운 한국어 음성으로
읽어 줍니다. 읽을 채널을 따로 고를 필요가 없습니다. 마이크가 없어도, 지금 말할
상황이 아니어도 타이핑만으로 대화에 낄 수 있습니다. 여성 5종·남성 5종 중 서버에
맞는 목소리를 고르고, 서버별 발음 사전으로 닉네임과 줄임말을 원하는 대로 교정할
수 있습니다.

■ 파티는 버튼으로
/파티모집 으로 제목만 적어도 모집글이 열립니다. 참가·대기·마감은 버튼으로
처리되고, 정원이 차면 대기열로 들어가 자리가 나는 대로 자동 승격됩니다. 시작
30분 전에 참가자를 부르고, 시작 시각이 되면 스스로 마감합니다. 롤·발로란트
모집은 참가자 티어를 모집글에 함께 보여 줍니다.

■ 서버가 살아 있는지 보이게
음성 참여 시간과 메시지 활동으로 주간 TOP 10을 만들어 지정한 채널에 자동으로
보냅니다. 점수는 서버 안에서만 계산되며 서버 밖으로 공유되지 않습니다.

■ 그 외
리그 오브 레전드·발로란트 전적과 랭크 조회, 오늘의 운세, 그리고 서버 관리자용
웹 대시보드를 제공합니다. 대시보드는 고정 비밀번호 없이 디스코드가 관리자 권한을
확인한 뒤 발급하는 5분·1회용 링크로만 열립니다.

코아는 무료이며, 소스 코드는 MIT 라이선스로 GitHub에 공개되어 있습니다.
```

### 태그

```text
한국어, TTS, 음성, 파티모집, 활동순위, 리더보드, 롤, 발로란트, 커뮤니티, 유틸리티
```

### 카테고리

`Utilities` 를 1순위로, 대안으로 `Social` 을 둡니다. 코아의 중심 기능(TTS·모집·순위)은
커뮤니티 운영 도구에 가깝습니다.

## 남은 작업 (우선순위)

1. **연락 수단 확정** — `site/privacy.html`, `site/terms.html` 의 `[작성 필요]` 블록 두 곳을
   실제 이메일 또는 지원 디스코드 서버 초대 링크로 교체합니다. 심사 필수 항목입니다.
2. **앱 인증 신청** — Developer Portal의 인증 상태와 신청 가능 여부를 확인합니다.
   계정 2단계 인증이 켜져 있어야 합니다.
3. **스크린샷 보강** — App Directory는 시각 자료의 비중이 큽니다. 최소한 파티 모집 실제
   화면과 활동 순위 발송 화면이 있으면 좋습니다.
4. **디스커버리 활성화** — 인증 완료 후 Developer Portal → **Discovery** 탭 →
   `Enable Discovery`.

## 참고 문서

- [App Directory Inclusion Guidelines](https://support-dev.discord.com/hc/en-us/articles/8852009977879-App-Directory-Inclusion-Guidelines)
- [App Directory: App Content Requirements Policy](https://support-dev.discord.com/hc/en-us/articles/9489299950487-App-Directory-App-Content-Requirements-Policy)
- [App Directory FAQ for Developers](https://support-dev.discord.com/hc/en-us/articles/9405344459415-App-Directory-FAQ-for-Developers)

> 위 요건은 2026년 8월 기준으로 확인한 내용입니다. 디스코드는 정책을 자주 바꾸므로 제출
> 직전에 Developer Portal의 실제 화면과 위 문서를 다시 확인하세요.
