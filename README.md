<div align="center">

<img src="site/assets/koa-brand-banner.webp" alt="코아 봇 브랜드 배너" width="900">

# 코아 (Koa)

**채팅을 읽어주고, 파티를 모아주고, 서버를 살려두는 한국어 디스코드 봇**

음성 채널에 사람은 모였는데 아무도 말을 안 하는 서버.
"롤 하실분" 한 줄이 스크롤에 묻혀버리는 서버.
이번 주에 누가 활동했는지 아무도 모르는 서버.

코아는 그 세 가지를 한 봇으로 해결합니다.

<br>

### [**➜ 내 서버에 코아 초대하기**](https://discord.com/oauth2/authorize?client_id=1499232840133509160&permissions=309274414080&scope=bot%20applications.commands)

[소개 홈페이지](https://enderpawar.github.io/Koa-Discord_bot/) · [명령어](#명령어-한눈에-보기) · [자주 막히는 부분](#자주-막히는-부분) · [직접 호스팅](#직접-호스팅하기)

<br>

[![CI](https://github.com/enderpawar/Koa-Discord_bot/actions/workflows/ci.yml/badge.svg)](https://github.com/enderpawar/Koa-Discord_bot/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![discord.py](https://img.shields.io/badge/discord.py-2.7.1-5865F2?logo=discord&logoColor=white)
![한국어 음성](https://img.shields.io/badge/%ED%95%9C%EA%B5%AD%EC%96%B4%20%EC%9D%8C%EC%84%B1-10%EC%A2%85-0078D4?logo=microsoftazure&logoColor=white)
![단위 테스트](https://img.shields.io/badge/%EB%8B%A8%EC%9C%84%20%ED%85%8C%EC%8A%A4%ED%8A%B8-428%EA%B0%9C-brightgreen)
[![License: MIT](https://img.shields.io/badge/license-MIT-black)](LICENSE)

</div>

---

## 30초 안에 첫 소리 듣기

초대 링크를 누르고 서버를 고른 다음, 디스코드에서 이것만 하면 끝입니다.

```text
1.  아무 음성 채널에나 들어간다
2.  /입장
3.  그 채널 채팅에 아무 말이나 쓴다   ->  코아가 읽어준다
```

설정 화면 없음, 읽을 채널 고르기 없음, 계정 연동 없음. `/입장` 한 번이 전부입니다.
음성 채널에 들어가면 뜨는 **`TTS 모드`** 버튼을 눌러도 똑같이 시작됩니다.

> 코아가 들어오면 그 음성 채널 채팅에 `코아 왔어요` 하고 인사를 남깁니다.
> 명령 응답은 실행한 사람에게만 보이니까, 채널에 있는 다른 사람도 알 수 있게 따로 보내는 겁니다.

---

## 서버장이 코아를 쓰는 이유

| 서버에서 늘 생기는 일 | 코아가 하는 일 |
|---|---|
| 음성 채널에 모여 있는데 마이크 없는 사람은 대화에서 빠진다 | 채팅을 **한국어 TTS로 읽어줍니다.** 타이핑만 해도 대화에 낄 수 있습니다 |
| "롤 5인 구함" 한 줄이 채팅에 묻히고, 몇 명 모였는지 아무도 모른다 | **버튼식 파티 모집.** 참가·대기·마감이 자동, 정원 차면 대기열, 자리 나면 자동 승격 |
| 서버가 조용해지는데 누가 활동하고 있는지 알 방법이 없다 | **주간 활동 랭킹.** 음성 70% + 메시지 30%로 TOP 10, 매주 금요일 초기화 |
| 봇 설정하려고 웹 대시보드에 또 로그인해야 한다 | `/관리자 대시보드` 한 번이면 **5분짜리 일회용 링크**를 발급합니다. 비밀번호도, 고정 토큰도 없습니다 |
| 봇이 새벽에 조용히 죽어서 아침에 발견한다 | 음성 연결이 끊기면 **자동 복구**, 사람 다 나가면 대기열 정리하고 스스로 퇴장 |
| 닉네임·줄임말을 봇이 엉뚱하게 읽는다 | **서버별 발음 사전.** 대시보드에서 원문과 읽을 말을 등록하면 그대로 읽습니다 |

그리고 이런 것들이 옵션으로 붙습니다.

- **롤 · 발로란트 전적** — 라이엇 ID를 등록하면 랭크와 최근 경기를 디스코드 안에서 조회
- **파티 티어 뱃지** — 모집 제목에 `롤`, `발로`가 들어가면 참가자 줄에 티어가 자동으로 붙습니다
- **오늘의 운세** — 게임운·관계운, 같은 날엔 같은 결과 (외부 API 없는 순수 오락 기능)
- **유튜브 음악** — 코드는 들어 있고 기본 꺼짐. 직접 호스팅할 때 `MUSIC_ENABLED=1`로 켤 수 있습니다

---

## 실제로 이렇게 보입니다

### 파티 모집 — 채팅 한 줄과 같은 속도로
<img width="1453" height="221" alt="image" src="https://github.com/user-attachments/assets/0991b1a9-1298-4fbe-a743-26988e94ec48" />

`/파티모집`을 치면 입력 폼이 뜹니다. 다섯 칸이 한 화면에 나오고, **제목만 쓰고 제출하면
`지금 바로 · 인원 제한 없음`으로 열립니다.** 제목 칸의 흐린 글씨는 이 서버에서 가장 최근에
쓴 제목이라, 뭘 적는 칸인지 바로 보입니다.

<img width="803" height="1275" alt="image" src="https://github.com/user-attachments/assets/b1e6569f-1661-48a5-a6d5-1afe997afe49" />


제출하면 채널에 이런 모집글이 올라갑니다.

<img width="1315" height="594" alt="image" src="https://github.com/user-attachments/assets/4b907349-6108-43f5-9ad3-05da0d8e3cd2" />


버튼만 누르면 됩니다. 정원이 차면 다음 사람은 대기열로 가고, 누가 취소하면 가장 먼저
기다린 사람이 자동으로 올라옵니다. **시작 30분 전에 참가자를 부르고, 시작 시각이 되면
스스로 마감합니다.** 봇이 재시작돼도 열려 있던 모집은 그대로 복원됩니다.

### 관리자 대시보드 — 링크 하나로 들어가는 서버별 설정

<table>
  <tr>
    <td align="center"><strong>발송 채널·시각 설정</strong></td>
    <td align="center"><strong>저장 완료</strong></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/admin-leaderboard-settings.png" alt="코아 관리자 대시보드의 일일 리더보드 설정 화면" width="480"></td>
    <td><img src="docs/screenshots/admin-leaderboard-saved.png" alt="코아 관리자 대시보드에서 리더보드 설정 저장을 완료한 화면" width="480"></td>
  </tr>
</table>

### 코아 초대 화면

<p align="center">
  <img src="docs/screenshots/discord-bot-invite.png" alt="디스코드에서 Koa_Bot을 서버에 추가하는 봇 초대 화면" width="520">
</p>

### 음악 모드와 오류 안내 (직접 호스팅 시 옵션)

음악 모드가 켜지면 일반 채팅 TTS를 멈추고 `/재생` 요청만 받습니다. 공개 영상이 아니거나
불러올 수 없는 주소는 원인을 바로 알려줍니다.

<p align="center">
  <img src="docs/screenshots/discord-music-mode.png" alt="디스코드에서 코아 음악 모드를 활성화하고 유튜브 불러오기 오류를 안내하는 화면" width="520">
</p>

---

## 서버장이 처음에 확인할 것 세 가지

초대만 해도 TTS는 바로 됩니다. 나머지는 원할 때 켜면 됩니다.

**1. 권한이 다 들어갔는지** — 초대 링크에 이미 포함돼 있지만, 채널별 권한 오버라이드가
걸려 있으면 막힐 수 있습니다. 코아 역할에 `채널 보기`, `메시지 보내기`, `메시지 기록 보기`,
`공개 스레드 만들기`, `연결`, `말하기`가 있는지 확인하세요.

**2. `/목소리`로 서버 목소리 고르기** — 여성 5종·남성 5종, 총 10종입니다. `채널 관리`
권한이 있는 사람만 바꿀 수 있고, 서버 단위로 저장됩니다.

**3. `/관리자 대시보드`에서 리더보드 채널 지정** — 정해진 시각에 활동 랭킹을 자동으로
보낼 채널을 정합니다. 발음 사전도 여기서 편집합니다.

<details>
<summary><b>알림 역할 목록에 무엇이 뜨는지</b></summary>

`/파티모집` 폼의 `알림 역할`에는 **멘션 허용이 켜진 역할만** 뜹니다. 서버 설정 → 역할에서
`누구나 이 역할을 멘션할 수 있습니다`를 켜 두면 목록에 나타납니다.

시간대·등급처럼 멘션 대상이 아닌 역할까지 늘어놓으면 정작 부르려던 게임 역할이
드롭다운 25칸에 밀려 안 보이기 때문에, 서버가 멘션을 허용한 것만 후보로 씁니다.

멘션 허용 역할이 하나도 없는 서버에서는 Discord 기본 역할 선택기가 뜹니다. 이 경우
멘션 허용이 꺼진 역할을 부르려면 코아에게 `Mention @everyone, @here, and All Roles`
권한이 필요하고, 없으면 파티는 그대로 열리되 알림만 생략되며 모집자에게만 알려줍니다.
</details>

---

## 명령어 한눈에 보기

### 음성

| 명령어 | 권한 | 설명 |
|---|---:|---|
| `/입장` | 누구나 | 지금 들어가 있는 음성 채널로 코아를 부르고 그 채널 채팅 읽기를 켭니다 |
| `/퇴장` | 누구나 | 코아를 음성 채널에서 내보냅니다 |
| `/상태` | 누구나 | 현재 연결 채널과 TTS 설정을 확인합니다 |
| `/목소리` | 채널 관리 | 여성 5종·남성 5종 중 이 서버의 목소리를 고릅니다 |

### 커뮤니티

| 명령어 | 권한 | 설명 |
|---|---:|---|
| `/파티모집` | 누구나 | 제목·시작·정원·메모·알림 역할을 한 화면에서 채워 파티를 엽니다 |
| `/파티목록` | 누구나 | 현재 모집 중인 파티를 봅니다 |
| `/내파티` | 누구나 | 내가 참가하거나 대기 중인 파티만 봅니다 |
| `/파티취소` | 모집자 | 내가 연 모집을 목록에서 골라 없앱니다 |
| `/활동점수` | 누구나 | 내 활동 점수 또는 멤버 활동 점수를 봅니다 |
| `/활동순위` | 누구나 | 이번 주 서버 활동 TOP 10을 봅니다 |
| `/오늘의운세` | 누구나 | 오늘의 게임운·관계운과 행운 포인트를 봅니다 |

### 게임 · 관리

| 명령어 | 권한 | 설명 |
|---|---:|---|
| `/롤 등록·전적·검색·등록해제` | 누구나 | 라이엇 ID를 등록하고 롤 솔로 랭크와 최근 경기를 조회합니다 |
| `/발로란트 등록·전적·검색·등록해제` | 누구나 | 라이엇 ID를 등록하고 발로란트 경쟁전 티어와 최근 경기를 조회합니다 |
| `/관리자 대시보드` | 관리자 | 이 서버에만 유효한 5분짜리 일회용 대시보드 링크를 엽니다 |

라이엇 ID 등록은 **서버별로 따로** 관리됩니다.

<details>
<summary><b>음악 명령어</b> (직접 호스팅 + <code>MUSIC_ENABLED=1</code>일 때만 등록됩니다)</summary>

| 명령어 | 권한 | 설명 |
|---|---:|---|
| `/재생 주소:<URL>` | 같은 음성방 | 공개된 단일 유튜브 영상을 재생하거나 대기열에 추가합니다 |
| `/스킵` | 같은 음성방 | 현재 곡을 넘깁니다 |
| `/중지` | 같은 음성방 | 현재 곡과 대기열을 모두 정리합니다 |
| `/재생목록` | 같은 음성방 | 재생 중인 곡과 남은 대기열을 봅니다 |

음악 모드에서는 일반 채팅과 입·퇴장 알림을 TTS 큐에 넣지 않습니다. 음성 패널의 `TTS 모드`
버튼으로 돌아올 수 있고, 전환할 때 이전 모드의 재생과 대기열은 정리됩니다. 두 모드는 동시에
켜지지 않습니다. 유튜브 **검색어·재생목록 URL·라이브는 지원하지 않습니다.**

기본값은 꺼짐입니다. 꺼진 상태에서는 음악 Cog와 관련 슬래시 명령을 아예 등록하지 않고,
저장돼 있던 음악 모드도 TTS 모드로 처리합니다.
</details>

---

## 안심하고 초대해도 되는 이유

**서버 데이터가 서버 밖으로 나가지 않습니다.** 활동 점수와 랭킹은 그 서버 안에서만
계산되고, 모든 설정·발음 사전·게임 계정 등록은 `guild_id`로 격리됩니다. 다른 서버의
설정이 섞이지 않습니다.

**대시보드에 고정 비밀번호가 없습니다.** `/관리자 대시보드`를 실행하면 디스코드가 관리자
권한을 확인한 뒤 일회용 링크를 비공개로 발급합니다. 링크는 **5분 유효 · 1회용**이고, 발급
당시의 관리자와 서버 하나에만 묶이며, 처음 쓰이는 순간 폐기됩니다. URL에 서버 ID나 서버
이름이 드러나지 않습니다. 로그인 뒤 세션은 **유휴 15분 / 최대 30분**에 만료되고, 모든
요청마다 관리자 권한을 다시 확인하므로 서버에서 권한을 잃으면 남아 있던 세션도 다음
요청에서 끊깁니다.

> 링크 자체가 짧게 유효한 로그인 권한입니다. 다른 사람에게 전달하거나 화면을 공유하지 마세요.

**조용히 죽지 않습니다.** 일시적인 디스코드 음성 연결 끊김은 자동 복구하고, 3초 이상 밀린
오래된 TTS 요청은 건너뛰어 연속 채팅에서 응답 지연이 계속 쌓이지 않게 합니다. TTS를 끄거나
`/퇴장`하기 전까지 연결을 유지해 첫 문장 cold start도 없습니다. 마지막 사용자가 나가
코아만 남으면 대기 중인 TTS를 정리하고 자동 퇴장하며, **사람이 없는 음성 채널에는 채팅이
올라와도 다시 들어가지 않습니다.**

**전적 API가 죽어도 봇은 삽니다.** 라이엇 API 키가 없거나 만료돼도 다른 기능은 정상
로드되고, 전적 명령만 설정 안내를 표시합니다. 파티 티어 뱃지는 조회에 실패하면 뱃지만
빠지고 모집은 그대로 동작합니다.

**428개 단위 테스트가 푸시마다 돌아갑니다.** ([CI 워크플로](.github/workflows/ci.yml))

---

## 기능 자세히 보기

<details>
<summary><b>메시지를 읽기 전에 이렇게 정리합니다</b></summary>

- 다른 봇이나 웹훅 메시지는 무시
- 멘션은 표시명으로 읽기
- URL은 `링크`로 읽기
- 마크다운 문법 제거
- `ㅋㅋㅋ`, `ㅠㅠ` 같은 자음 반응은 소리나는 대로 읽기
- 물음표만 보낸 메시지(`?`, `??`, `????`)는 개수와 상관없이 `으음?!` 한 번으로 읽기
- 문장 끝 물음표는 그대로 두기 (억양은 TTS가 처리)
- 서버 발음 사전에 등록한 말은 바꿔서 읽기
- 너무 긴 메시지는 200자 근처에서 자르기
- 첨부만 있는 빈 메시지는 읽지 않기

짧은 문장 뒤에 Azure가 붙이는 긴 무음도 자동으로 제거합니다.

> 발음 사전 규칙은 **마크다운을 제거한 뒤**에 적용됩니다. `**ㅇㅈ**`처럼 감싼 경우
> 원문(`ㅇㅈ`)만 등록하세요.
</details>

<details>
<summary><b>파티 모집 — 시간 입력, 마감과 취소의 차이, 스레드</b></summary>

**시간은 자유 입력입니다.** `지금`(비우면 같음), `30분 뒤`, `21:00`, `오늘 21:00`,
`내일 19:30`, `2026-09-01 20:00` 을 모두 받습니다. 시간만 적었고 오늘 이미 지난 시각이면
다음 날로 해석합니다.

**정원도 자유 입력입니다.** `5`, `5명` 처럼 적고, 비우거나 `제한 없음`이라고 쓰면 대기열
없이 계속 받습니다.

`시작`이나 `정원`을 잘못 적으면 무엇이 문제인지 알려주고 **`다시 입력` 버튼**을 띄웁니다.
이미 친 값이 그대로 채워진 폼이 다시 열리므로 처음부터 쓰지 않아도 됩니다.

**마감과 취소는 다릅니다.**
- `모집 마감` 버튼 — 더 이상 참가를 받지 않을 뿐 기록은 남습니다. 모집자와 `메시지 관리`
  권한자 누구나 누를 수 있습니다.
- `/파티취소` — 잘못 연 모집을 아예 없애는 동작이라 **모집자 본인만** 할 수 있고,
  참가자가 있었다면 취소 사실을 알려줍니다.

예약한 파티는 **시작 시각에**, 지금 시작하는 파티는 **2시간 뒤에** 자동 마감됩니다.
(시작 시각으로 닫으면 올리자마자 마감되기 때문입니다.) 마감·취소된 파티는 시작 시각으로부터
7일이 지나면 자동 정리되고, 진행 중인 모집은 정리 대상이 아닙니다.

**스레드** — 모집자와 현재 참가자는 `스레드 만들기` 버튼으로 공개 대화 스레드를 한 번만
만들 수 있습니다. 이미 만들어진 뒤 다시 누르면 같은 스레드로 안내합니다. 스레드는 24시간
동안 새 대화가 없으면 디스코드가 자동 보관하며, 모집 메시지와 같은 채널을 볼 수 있는 서버
멤버에게는 보일 수 있습니다. 대기 중인 사용자는 참가자로 승격된 뒤 만들 수 있습니다.

모집자는 항상 첫 참가자로 포함되고, 모집 임베드 상단에 모집자의 프로필 사진과 이름이
표시됩니다. 봇이 재시작되어도 `/data/party.db`에서 열린 모집을 복원합니다.
</details>

<details>
<summary><b>파티 티어 뱃지 — 언제 붙고 언제 안 붙나</b></summary>

`롤 듀오`, `발로 3인`처럼 **모집 제목에 게임 이름이 들어가면** 참가자 줄에 티어가 함께
표시되고, 참가자 목록 제목에 `참가자 · 🥇골드 2 · 🥈실버 1` 같은 구성 요약이 붙습니다.
제목에서 게임을 찾지 못하면 (`저녁 먹자`) 아무것도 붙지 않습니다.

- 티어는 `/롤 등록` · `/발로란트 등록`으로 라이엇 ID를 묶어 둔 사람만 나옵니다.
  등록을 해제하면 뱃지도 바로 사라집니다.
- 롤은 **솔로 랭크만** 봅니다. 자유 랭크만 있으면 `언랭`으로 표시됩니다.
- 조회 결과는 24시간 캐시합니다. 참가 버튼을 누른 사람의 티어만 갱신하므로 API 호출이
  인원 수만큼 늘어나지 않습니다.
- 조회에 실패하거나 API 키가 없으면 뱃지만 빠지고 모집은 그대로 동작합니다.
- 서버별로 끄려면 관리자 대시보드의 `파티 티어 뱃지`를 해제하세요.
- 롤체(TFT)는 소환사 랭크와 무관하므로 제목에 `롤`이 있어도 제외합니다.

**티어 그림을 게임사 공식 아이콘으로 바꾸기** (선택, 직접 호스팅 시 한 번만 실행)

봇 애플리케이션에 이모지를 올리는 것이라 초대된 서버의 이모지 목록은 건드리지 않습니다.

```bash
python scripts/sync_tier_emojis.py --dry-run        # 무엇을 올릴지 확인
python scripts/sync_tier_emojis.py                  # 롤 + 발로란트 전부
python scripts/sync_tier_emojis.py --game valorant  # 한 게임만
```

총 37개입니다 — 롤 11개(아이언~챌린저 + 언랭), 발로란트 26개. 롤은 골드 1~4가 엠블럼
하나를 공유하지만 발로란트는 단계마다 화살표 수가 달라 그림이 전부 다르므로, 롤은 티어
단위·발로란트는 단계 단위로 올립니다. 일부만 올라간 상태여도 없는 것만 유니코드로
대체되어 정상 동작합니다.

이미지 출처는 롤이 CommunityDragon의 공식 랭크 엠블럼, 발로란트가 valorant-api.com의 최신
경쟁전 티어 아이콘입니다(에피소드마다 주소가 바뀌어 실행 시점에 조회합니다). 올리지 않으면
유니코드 이모지(`🥇 골드 2`)로 표시됩니다.
</details>

<details>
<summary><b>활동 랭킹 계산 기준</b></summary>

랭킹은 서버 안에서만 계산됩니다.

- 음성 채널에 머문 시간 집계
- 메시지 개수 집계
- 점수는 `음성 70% + 메시지 30%`
- 서버 내 최고 음성 시간과 최고 메시지 수를 각각 100% 기준으로 환산
- 매주 **금요일 00:00 (KST)** 에 초기화
- 데이터는 기본적으로 `rank_stats.json`에 저장
</details>

<details>
<summary><b>오늘의 운세</b></summary>

`/오늘의운세`는 사용자 ID와 KST 날짜로 결과를 계산하므로 같은 날에는 같은 결과가 나옵니다.
기본 응답은 본인에게만 보이며 `서버에 공유` 버튼으로 공개할 수 있습니다.
외부 API를 사용하지 않는 오락용 콘텐츠입니다.
</details>

<details>
<summary><b>관리자 대시보드에서 할 수 있는 일</b></summary>

- 발음 사전 편집 (읽기 전에 바꿀 말 등록·삭제)
- 현재 연결된 읽기/음성 채널 확인 (읽기 전용)
- 일일 리더보드 채널 설정
- 리더보드 자동 발송 켜기/끄기
- 발송 시각 변경
- 리더보드 즉시 발송
- 리더보드 데이터 초기화
- 파티 티어 뱃지 켜기/끄기
</details>

---

## 자주 막히는 부분

| 증상 | 확인할 것 |
|---|---|
| 슬래시 명령이 안 보임 | 디스코드 앱을 완전히 닫고 다시 열기. 새 명령이 전체에 반영되는 데 최대 1시간 걸릴 수 있습니다 |
| 코아가 채팅을 못 읽음 | 먼저 음성 채널에 들어간 뒤 `/입장`을 했는지, 글을 쓴 곳이 **그 음성 채널의 채팅**인지 확인 |
| 음성 채널 입장 실패 | 코아 역할의 `연결`, `말하기` 권한과 **채널별 권한 오버라이드** 확인 |
| 빈 음성 채널에 안 들어옴 | 정상입니다. 사람이 먼저 참여한 뒤 `/입장`을 실행해야 합니다 |
| 한글 발음이 이상함 | `/목소리`로 다른 보이스 선택, 또는 대시보드 발음 사전에 규칙 추가 |
| 발음 사전을 저장했는데 그대로 읽음 | 규칙은 마크다운 제거 뒤에 적용됩니다. `**ㅇㅈ**`처럼 감싼 경우 원문만 등록하세요 |
| TTS 설정 명령이 거부됨 | 실행한 사용자에게 `채널 관리` 권한이 있는지 확인 |
| 대시보드 링크가 "만료되었거나 이미 사용" | 링크는 5분·1회용입니다. `/관리자 대시보드`로 새로 받으세요 |
| 대시보드가 자꾸 로그인 화면으로 돌아감 | 세션은 유휴 15분·최대 30분입니다. 서버 관리자 권한이 유지되는지도 확인 |
| 파티 `알림 역할` 목록에 원하는 역할이 없음 | 서버 설정 → 역할에서 그 역할의 멘션 허용을 켜세요 |
| 파티 알림 역할을 골랐는데 알림이 안 감 | 역할의 멘션 허용을 켜거나 코아에게 `모든 역할 멘션` 권한 부여 |
| 파티 스레드가 만들어지지 않음 | 코아에게 `공개 스레드 만들기` 권한을 주고, 모집글이 일반 텍스트 채널에 있는지 확인 |

<details>
<summary><b>직접 호스팅할 때 추가로 겪는 문제</b></summary>

| 증상 | 확인할 것 |
|---|---|
| `FFmpeg가 PATH에 없습니다` | FFmpeg 설치 후 **새 터미널**에서 다시 실행 |
| 슬래시 명령이 안 보임 | `applications.commands` 스코프, 전역 명령 반영 대기, `TEST_GUILD_ID` 사용 |
| 봇이 채팅을 못 읽음 | Message Content Intent 켰는지 확인 |
| 입장/퇴장 알림이 안 됨 | Server Members Intent 켰는지 확인 |
| 공개 유튜브 영상도 불러오기 실패 | PO Token provider가 healthy인지 확인. IP 차단이 지속되면 `YOUTUBE_PROXY_URL` production secret에 인증 HTTP(S) 프록시를 등록하고 재배포 |
| 음악 제목은 뜨지만 소리가 안 남 | yt-dlp가 반환한 HTTP 헤더를 쓰는 최신 버전인지 확인하고 봇 로그의 FFmpeg 오류 확인 |

자세한 로그가 필요하면 `.env`에서 `LOG_LEVEL=DEBUG`로 바꿉니다.

Oracle 배포에서는 `docker-compose.yml`이 yt-dlp용 PO Token provider를 내부 서비스로 함께
실행합니다. 외부 포트나 유튜브 계정 쿠키는 사용하지 않습니다. 상태 확인:

```bash
docker inspect --format '{{.State.Health.Status}}' koa-youtube-pot-provider
docker logs koa-youtube-pot-provider
```

Oracle 공인 IP 자체가 유튜브에 차단된 경우에는 GitHub production secret `YOUTUBE_PROXY_URL`에
`http://user:password@host:port` 형식의 ISP/레지덴셜 프록시를 등록합니다. 배포 과정이 이를
`.env`로 전달하며 yt-dlp와 FFmpeg가 같은 프록시를 사용합니다.
**실제 자격 증명은 `.env.example`이나 Git에 커밋하지 마세요.**
</details>

---

## 직접 호스팅하기

운영 중인 코아를 [초대](https://discord.com/oauth2/authorize?client_id=1499232840133509160&permissions=309274414080&scope=bot%20applications.commands)하면 아래 과정은 필요 없습니다.
직접 굴리고 싶거나 코드를 고쳐 쓰고 싶을 때만 보세요.

### 1. 필요한 것

- Python 3.10 이상
- FFmpeg
- 디스코드 봇 토큰
- Azure Speech 리소스 키와 리전

```bash
# Windows
winget install --id=Gyan.FFmpeg

# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt install -y ffmpeg
```

설치 후 새 터미널에서 `ffmpeg -version`으로 확인합니다.

### 2. 패키지 설치

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

### 3. `.env` 만들기

프로젝트 루트에 `.env`를 만들고 채웁니다. 전체 항목은 [`.env.example`](.env.example) 참고.

```ini
DISCORD_TOKEN=디스코드_봇_토큰
AZURE_SPEECH_KEY=Azure_Speech_키
AZURE_SPEECH_REGION=koreacentral
LOG_LEVEL=INFO

# 선택: 게임 전적 조회
# RIOT_API_KEY=RGAPI-...
# VALORANT_API_KEY=...
# LOL_DEFAULT_PLATFORM=kr
# VALORANT_DEFAULT_REGION=kr

# 선택: 유튜브 음악 (기본 꺼짐)
# MUSIC_ENABLED=1

# 선택: 개발 서버에 슬래시 명령을 바로 반영
# TEST_GUILD_ID=123456789012345678
```

> `.env`에는 토큰이 들어갑니다. 절대 공개 저장소에 올리지 마세요.

### 4. 실행

```bash
python bot.py
```

콘솔에 `logged in as ...`와 `synced ... slash commands`가 보이면 준비 끝입니다.

<details>
<summary><b>디스코드 봇 초대 설정</b></summary>

Discord Developer Portal에서 봇을 만들고 확인하세요.

1. **Bot** 탭에서 토큰을 발급해 `.env`의 `DISCORD_TOKEN`에 넣기
2. **Privileged Gateway Intents** 2개 켜기 — `Server Members Intent`, `Message Content Intent`
3. **OAuth2 → URL Generator**
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: `View Channels`, `Send Messages`, `Read Message History`,
     `Create Public Threads`, `Send Messages in Threads`, `Connect`, `Speak`,
     `Use Voice Activity`, `Use Slash Commands`
4. 생성된 URL로 봇을 서버에 초대

권한이 빠지면 슬래시 명령이 안 보이거나, 메시지를 못 읽거나, 음성 채널에 못 들어갈 수 있습니다.
</details>

<details>
<summary><b>관리자 웹 대시보드 켜기</b></summary>

`docker-compose.yml`로 띄우면 아래 값은 compose가 알아서 넣습니다. 직접 `python bot.py`로
띄울 때만 `.env`에 적으면 됩니다.

```ini
ADMIN_WEB_ENABLED=1
ADMIN_WEB_HOST=127.0.0.1
ADMIN_WEB_PORT=8080

# 일회용 권한의 해시만 저장하는 SQLite 파일 (Docker 기본값: /data/admin_login.sqlite3)
# ADMIN_LOGIN_DB_PATH=admin_login.sqlite3

# 배포 환경에서 /관리자 대시보드 버튼에 보여줄 공개 주소
# ADMIN_WEB_PUBLIC_URL=https://your-admin.example.com

# 리버스 프록시 뒤에서 실제 접속자 IP로 로그인 남용을 집계할 때 신뢰할 대역
# ADMIN_WEB_TRUSTED_PROXIES=172.16.0.0/12
```

로컬 기본 주소는 `http://127.0.0.1:8080`입니다.

공개 주소에는 Cloudflare Tunnel 같은 HTTPS 종단을 사용하세요. 대시보드는 고정 키나 마스터
토큰을 받지 않으며, 디스코드에서 발급한 일회용 링크로만 로그인할 수 있습니다.
</details>

<details>
<summary><b>게임 전적 API 키 발급</b></summary>

- **롤** — Riot Games 공식 API. `RIOT_API_KEY`를
  [Riot Developer Portal](https://developer.riotgames.com/)에서 발급하세요. 로그인 시 개발
  키가 생성되지만 **24시간마다 만료**되므로, 계속 운영할 봇은 프로젝트를 등록해 Personal
  또는 Production 키 승인을 받아야 합니다.
- **발로란트** — 개인용 공식 API 키가 제공되지 않아 HenrikDev API를 사용합니다.
  `VALORANT_API_KEY`는 [HenrikDev Discord](https://discord.com/invite/X3GaVkX2YN)에 가입·인증한
  뒤 `#get-a-key`에서 `VALORANT (Basic Key)`를 선택해 발급하세요.

API 키가 없거나 만료된 경우에도 봇의 다른 기능은 정상 로드되고, 전적 명령만 설정 안내를
표시합니다.
</details>

### 배포

코아는 파이썬 프로세스 하나로 동작합니다. 디스코드 음성 송출은 상시 WebSocket 연결과 UDP
스트리밍을 요구하므로 **서버리스(Lambda, Cloudflare Workers 등)로는 운영할 수 없습니다.**
봇이 계속 켜져 있으려면 PC나 VM 같은 실행 환경이 계속 살아 있어야 합니다.

- **Oracle Cloud 배포 (현행)** — [docs/deploy-oracle.md](docs/deploy-oracle.md)
- 직접 서버 운영 — `python bot.py`
- 개인 PC 운영 — PC가 꺼지면 봇도 꺼집니다

---

## 개발

```bash
pip install -r requirements-dev.txt
python -m pytest tests/unit -q
```

라이브 Azure Speech 테스트:

```bash
RUN_LIVE=1 python -m pytest tests/unit -m live -q
```

<details>
<summary><b>Windows에서 <code>PermissionError</code>로 무더기 실패한다면</b></summary>

임시 폴더 권한 문제입니다. 임시 폴더를 저장소 안으로 돌려서 실행하세요.

```powershell
$env:TEMP="$PWD\.pytest-tmp"; $env:TMP=$env:TEMP
python -m pytest tests/unit -q --basetemp=.pytest-tmp/base
```
</details>

문서:

- [docs/pipeline.md](docs/pipeline.md) — Phase 구성과 의존 그래프
- [docs/testing.md](docs/testing.md) — 파일→테스트 매핑
- [docs/discord-environment-testing.md](docs/discord-environment-testing.md) — 라이브 환경 검증 절차
- [docs/deploy-oracle.md](docs/deploy-oracle.md) — Oracle Cloud 배포
- [docs/app-directory.md](docs/app-directory.md) — App Directory 등재 준비
- [CLAUDE.md](CLAUDE.md) — 에이전트 작업 규칙
- [site/](site/) — [소개 홈페이지](https://enderpawar.github.io/Koa-Discord_bot/) 소스

---

## 라이선스

[MIT License](LICENSE) — 자유롭게 자체 호스팅·수정·재배포할 수 있습니다.

MIT는 코드에 대한 권리만 부여합니다. **"코아"라는 이름과 캐릭터·이미지 등 브랜드 자산은
포함되지 않습니다.** 포크해서 별도 봇을 운영하실 경우 다른 이름과 아이콘을 사용해 주세요.

운영 중인 공식 인스턴스에는 [이용약관](https://enderpawar.github.io/Koa-Discord_bot/terms.html)과
[개인정보처리방침](https://enderpawar.github.io/Koa-Discord_bot/privacy.html)이 적용됩니다.

---

<div align="center">

**음성 채널에 들어가서 `/입장` 한 번이면 됩니다.**

### [➜ 코아 초대하기](https://discord.com/oauth2/authorize?client_id=1499232840133509160&permissions=309274414080&scope=bot%20applications.commands)

<img src="site/assets/koa-brand-profile.webp" alt="헤드셋을 착용한 코아 봇 캐릭터" width="200">

</div>
