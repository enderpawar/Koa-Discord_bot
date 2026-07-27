# Nothing Bot

디스코드 서버에서 채팅을 한국어 음성으로 읽어주고, 음성 채널 입장/퇴장도 알려주고, 이번 주 활동 랭킹까지 보여주는 서버용 봇입니다.

채팅방에 글을 쓰면 봇이 음성 채널에서 읽어줍니다. 회의, 게임, 작업방, 라디오처럼 틀어두는 서버에 잘 맞습니다.

## 이런 일을 해요

- 지정한 텍스트 채널 메시지를 한국어 TTS로 읽기
- 지정한 음성 채널 입장/퇴장 안내
- TTS를 끄거나 `/퇴장` 하기 전까지 연결 유지(첫 문장 cold start 방지)
- 일시적인 Discord 음성 연결 끊김은 자동 복구
- 이번 주 활동 점수, 개인 랭크, TOP 10 리더보드 제공
- 버튼으로 파티 참가·대기·취소·마감, 시작 전 알림 제공
- KST 날짜별로 고정되는 게임·관계 중심 오늘의 운세
- 리그 오브 레전드와 발로란트 계정 등록·랭크·최근 경기 조회
- 관리자 웹 대시보드로 TTS/리더보드 설정 관리

## 빠른 사용법

봇을 서버에 초대한 뒤 Discord에서 아래 순서대로 입력하면 됩니다.

```text
1. 원하는 음성 채널에 먼저 입장
/입장
```

이후 해당 음성 채널의 채팅에 메시지를 쓰면 봇이 바로 읽어줍니다.

음성 채널에 들어갔을 때 표시되는 `TTS 켜기` 버튼을 눌러도 같은 방식으로 시작할 수 있습니다.

## 명령어

| 명령어 | 권한 | 설명 |
|---|---:|---|
| `/읽기채널` | 채널 관리 | 봇이 읽을 채팅 채널을 정합니다. |
| `/음성채널` | 채널 관리 | 봇이 말할 음성 채널을 정합니다. |
| `/목소리` | 채널 관리 | 여성 5종·남성 5종 중 TTS 목소리를 선택합니다. |
| `/입장` | 누구나 | 현재 참여 중인 음성 채널로 봇을 부르고 그 채널 채팅 TTS를 켭니다. |
| `/퇴장` | 누구나 | 봇을 음성 채널에서 내보냅니다. |
| `/상태` | 누구나 | 현재 TTS 설정을 확인합니다. |
| `/활동점수` | 누구나 | 내 활동 점수 또는 멤버 활동 점수를 봅니다. |
| `/활동순위` | 누구나 | 이번 주 서버 활동 TOP 10을 봅니다. |
| `/파티모집` | 누구나 | 게임·정원·시작 시간을 정해 파티원을 모집합니다. |
| `/파티목록` | 누구나 | 현재 모집 중인 파티를 봅니다. |
| `/내파티` | 누구나 | 내가 참가하거나 대기 중인 파티를 봅니다. |
| `/오늘의운세` | 누구나 | 오늘의 게임운·관계운과 행운 포인트를 봅니다. |
| `/롤 등록·전적·검색·등록해제` | 누구나 | 라이엇ID를 등록하거나 롤 랭크와 최근 경기를 조회합니다. |
| `/발로란트 등록·전적·검색·등록해제` | 누구나 | 라이엇ID를 등록하거나 발로란트 랭크와 최근 경기를 조회합니다. |
| `/마크 켜기·끄기·상태` | 누구나 | 암호로 마인크래프트 서버 전원을 제어하고 상태를 봅니다. |
| `/마크 화이트리스트 등록 <닉네임>` | 암호 필요 | Minecraft Java 닉네임을 운영 서버 화이트리스트에 등록합니다. |
| `/마크 서버 공지` | 누구나 | 접속 안내와 최신 서버 공지 게시판 링크를 표시합니다. |
| `/관리자 대시보드` | 관리자 | 웹 관리자 대시보드 링크를 엽니다. |

음성 채널에서 마지막 사용자가 나가 봇만 남으면 대기 중인 TTS를 정리하고 자동으로
퇴장합니다. 사람이 없는 음성 채널에는 채팅이 입력되어도 자동으로 재입장하지 않습니다.
짧은 문장 뒤에 Azure가 붙이는 긴 무음은 자동으로 제거하며, 3초 이상 밀린 오래된
요청은 건너뛰어 연속 채팅의 응답 지연이 계속 증가하지 않게 합니다.

선택 가능한 목소리는 여성 5종과 남성 5종, 총 10종입니다.

### 파티 모집과 오늘의 운세

파티 모집은 모집자를 첫 참가자로 포함합니다. 정원이 차면 이후 참가자는 대기열에
들어가고, 기존 참가자가 취소하면 가장 먼저 기다린 사용자가 자동으로 참가자로
이동합니다. 시작 30분 전 참가자에게 알리고 시작 시각이 되면 모집을 자동 마감합니다.
봇이 재시작되어도 `/data/party.db`에서 열린 모집을 복원합니다.

```text
/파티모집 게임:롤 정원:5 시작:오늘 21:00 메모:일반 즐겜
```

시작 시간은 `오늘 21:00`, `내일 19:30`, `21:00`, `2026-08-01 20:00`
형식을 지원합니다. 시간만 입력했고 오늘 이미 지난 시각이면 다음 날로 해석합니다.

`/오늘의운세`는 사용자 ID와 KST 날짜로 결과를 계산하므로 같은 날에는 같은 결과가
나옵니다. 기본 응답은 본인에게만 보이며 `서버에 공유` 버튼을 눌러 공개할 수 있습니다.
운세는 외부 API를 사용하지 않는 오락용 콘텐츠입니다.

### 게임 전적 API

- 롤은 Riot Games 공식 API를 사용합니다. `RIOT_API_KEY`를
  [Riot Developer Portal](https://developer.riotgames.com/)에서 발급하세요. 로그인 시
  개발 키가 생성되지만 24시간마다 만료되므로, 계속 운영할 봇은 프로젝트를 등록해
  Personal 또는 Production 키 승인을 받아야 합니다.
- 발로란트는 개인용 공식 API 키가 제공되지 않아 HenrikDev API를 사용합니다.
  `VALORANT_API_KEY`는 [HenrikDev Discord](https://discord.com/invite/X3GaVkX2YN)에
  가입·인증한 뒤 `#get-a-key`에서 `VALORANT (Basic Key)`를 선택해 발급하세요.
- API 키가 없거나 만료된 경우에도 봇의 다른 기능은 정상적으로 로드되고, 전적 명령만
  설정 안내를 표시합니다.

## 설치하기

### 1. 필요한 것

- Python 3.10 이상
- FFmpeg
- Discord 봇 토큰
- Azure Speech 리소스 키와 리전

FFmpeg 설치 예시:

```bash
# Windows
winget install --id=Gyan.FFmpeg

# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt install -y ffmpeg
```

설치 후 새 터미널에서 확인합니다.

```bash
ffmpeg -version
```

### 2. Python 패키지 설치

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

macOS/Linux라면 가상환경 활성화 명령만 바꿔주세요.

```bash
source .venv/bin/activate
```

### 3. `.env` 만들기

프로젝트 루트에 `.env` 파일을 만들고 아래 값을 채웁니다.

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

# 선택: 개발 서버에 슬래시 명령을 바로 반영하고 싶을 때
# TEST_GUILD_ID=123456789012345678
```

`.env`에는 토큰이 들어가므로 절대 공개 저장소에 올리지 마세요.

### 4. 실행

```bash
python bot.py
```

콘솔에 `logged in as ...`와 `synced ... slash commands`가 보이면 준비 끝입니다.

## Discord 봇 초대 설정

Discord Developer Portal에서 봇을 만들고 아래 설정을 확인하세요.

1. **Bot** 탭에서 토큰을 발급해 `.env`의 `DISCORD_TOKEN`에 넣기
2. **Privileged Gateway Intents**에서 아래 2개 켜기
   - Server Members Intent
   - Message Content Intent
3. **OAuth2 -> URL Generator**에서 체크
   - Scopes: `bot`, `applications.commands`
   - Bot Permissions: `View Channels`, `Send Messages`, `Read Message History`, `Connect`, `Speak`, `Use Voice Activity`, `Use Slash Commands`
4. 생성된 URL로 봇을 서버에 초대

권한이 빠지면 슬래시 명령이 안 보이거나, 메시지를 못 읽거나, 음성 채널에 못 들어갈 수 있습니다.

## 관리자 웹 대시보드

웹 UI를 켜면 브라우저에서 서버별 설정을 관리할 수 있습니다.

```ini
ADMIN_WEB_TOKEN=긴_랜덤_관리자_토큰
ADMIN_WEB_HOST=127.0.0.1
ADMIN_WEB_PORT=8080

# 배포 환경에서 /관리자 대시보드 버튼에 보여줄 공개 주소
# ADMIN_WEB_PUBLIC_URL=https://your-admin.example.com

# 특정 서버만 웹 UI에 보이게 할 때
# ADMIN_WEB_GUILD_IDS=123456789012345678
```

로컬 기본 주소:

```text
http://127.0.0.1:8080
```

웹 대시보드에서 할 수 있는 일:

- TTS 입력 채널 변경
- 음성 출력 채널 변경
- 일일 리더보드 채널 설정
- 리더보드 자동 발송 켜기/끄기
- 발송 시각 변경
- 리더보드 즉시 발송
- 리더보드 데이터 초기화

공개 주소로 배포한다면 `ADMIN_WEB_TOKEN`은 길고 예측하기 어렵게 설정하세요.

## 활동 랭킹 기준

랭킹은 서버 안에서만 계산됩니다.

- 음성 채널에 머문 시간 집계
- 메시지 개수 집계
- 점수는 `음성 70% + 메시지 30%`
- 서버 내 최고 음성 시간과 최고 메시지 수를 각각 100% 기준으로 환산
- 매주 금요일 00:00(KST)에 초기화
- 데이터는 기본적으로 `rank_stats.json`에 저장

## 메시지는 이렇게 읽어요

봇이 읽기 전에 메시지를 살짝 정리합니다.

- 다른 봇이나 웹훅 메시지는 무시
- 멘션은 표시명으로 읽기
- URL은 `링크`로 읽기
- 마크다운 문법 제거
- 너무 긴 메시지는 200자 근처에서 자르기
- 첨부만 있는 빈 메시지는 읽지 않기

## 자주 막히는 부분

| 증상 | 확인할 것 |
|---|---|
| `FFmpeg가 PATH에 없습니다` | FFmpeg 설치 후 새 터미널에서 다시 실행 |
| 슬래시 명령이 안 보임 | `applications.commands` 권한, 전역 명령 반영 대기, `TEST_GUILD_ID` 사용 |
| 봇이 채팅을 못 읽음 | Message Content Intent 켰는지 확인 |
| 입장/퇴장 알림이 안 됨 | Server Members Intent 켰는지 확인 |
| 음성 채널 입장 실패 | `Connect`, `Speak` 권한과 채널 권한 오버라이드 확인 |
| 한글 발음이 이상함 | `/목소리`로 `ko-KR-*` 보이스 선택 |
| TTS 설정 명령이 거부됨 | 실행한 사용자가 `채널 관리` 권한을 가졌는지 확인 |

자세한 로그가 필요하면 `.env`에서 바꿉니다.

```ini
LOG_LEVEL=DEBUG
```

## 배포

봇은 Python 프로세스 하나로 동작합니다. Discord 음성 송출은 상시 WebSocket 연결과 UDP 스트리밍을 요구하므로 **서버리스(Lambda, Cloudflare Workers 등)로는 운영할 수 없습니다.** 봇이 계속 켜져 있으려면 PC, VM 같은 실행 환경이 계속 살아 있어야 합니다.

- **Oracle Cloud 배포 (현행): [docs/deploy-oracle.md](docs/deploy-oracle.md)**
- Railway 배포 (구): [docs/deploy-railway.md](docs/deploy-railway.md)
- 직접 서버 운영: `python bot.py`
- 개인 PC 운영: PC가 꺼지면 봇도 꺼집니다.

## 개발 메모

테스트 실행:

```bash
pip install -r requirements-dev.txt
python -m pytest tests/unit -q
```

라이브 Azure Speech 테스트:

```bash
RUN_LIVE=1 python -m pytest tests/unit -m live -q
```

관련 문서:

- [docs/testing.md](docs/testing.md)
- [docs/pipeline.md](docs/pipeline.md)
- [docs/discord-environment-testing.md](docs/discord-environment-testing.md)
- [CLAUDE.md](CLAUDE.md)
