# Railway 배포 가이드

> ⚠️ **현재 운영 환경이 아닙니다.** 본 프로젝트는 Oracle Cloud 로 이전했습니다 → [docs/deploy-oracle.md](deploy-oracle.md)
> 이 문서는 참고용으로 남겨 둡니다. 아래 §2 의 환경변수 표에는 `RANK_PATH` 가 누락되어 있어, 이 설정 그대로 운영하면 재배포마다 랭킹 데이터가 초기화됩니다.

본 문서는 Railway.app 의 무료 크레딧으로 본 Discord TTS 봇을 24시간 운영하는 단계별 절차입니다.

> 예상 비용: Railway Hobby 플랜의 무료 $5/월 크레딧 안에서 일반 운영 가능 (실측 약 $2~3/월).

---

## 0. 사전 준비

- [ ] GitHub 저장소 (현재: `enderpawar/Nothing_bot`)
- [ ] Discord Developer Portal 의 봇 토큰 발급 완료
- [ ] 봇이 테스트 서버에 초대됨 (README §Discord Application 참고)

---

## 1. Railway 프로젝트 생성

1. https://railway.app 접속 → **Login with GitHub**
2. 우상단 **+ New Project** → **Deploy from GitHub repo**
3. `Nothing_bot` 선택 → 초기 빌드가 자동으로 시작됨 (Dockerfile 자동 감지)

---

## 2. 환경변수 설정

프로젝트 → **Variables** 탭 → **+ New Variable** 으로 다음 추가:

| Key | Value |
|-----|-------|
| `DISCORD_TOKEN` | Developer Portal 에서 발급받은 봇 토큰 |
| `LOG_LEVEL` | `INFO` (기본 권장) |
| `CONFIG_PATH` | `/data/config.json` |
| `RANK_PATH` | `/data/rank_stats.json` |
| `PARTY_DB_PATH` | `/data/party.db` |
| `TEST_GUILD_ID` *(선택)* | 슬래시 명령 즉시 sync 할 길드 ID. 미설정 시 전역 sync (캐시 1시간) |

> ⚠️ `DISCORD_TOKEN` 은 절대 코드/저장소에 들어가면 안 됨. Railway Variables 안에서만 보관.

---

## 3. 영구 디스크 (Volume) 마운트 — guild 데이터 영속화

Railway 컨테이너는 재배포할 때마다 파일 시스템이 초기화됩니다. 설정, 랭킹,
파티 모집 상태를 보존하려면 Volume이 필요합니다.

1. 프로젝트 → 서비스 클릭 → **Settings** 탭 → **Volumes** 섹션
2. **+ Add Volume**:
   - **Mount Path**: `/data`
   - **Size**: `1 GB` (충분)
3. 저장 후 자동 재배포됨

`CONFIG_PATH`, `RANK_PATH`, `PARTY_DB_PATH`가 모두 이 volume을 가리키도록
설정되어 있어야 합니다(§2 참조).

---

## 4. 배포 확인

1. **Deployments** 탭 → 최신 빌드 상태 확인
2. **View Logs** 클릭 → 다음과 같은 로그가 보이면 성공:
   ```
   logged in as <봇이름> (id=...)
   synced N slash commands (global)
   loaded extension: cogs.tts_cog
   ```
3. Discord 서버 멤버 목록에서 봇 아이콘이 **초록색** (online) 인지 확인

---

## 5. 자동 재배포

GitHub `main` 브랜치에 push 할 때마다 Railway 가 자동으로 새 빌드를 만들고 무중단 교체합니다. 수동 트리거는 **Deployments** → **Redeploy**.

---

## 6. 운영 팁

### 로그 확인
Railway 대시보드의 **View Logs** 가 stdout 을 실시간 표시. `LOG_LEVEL=DEBUG` 로 변경하면 상세 게이트웨이 로그까지 출력 (Variables 수정 후 자동 재배포).

### 봇 재시작
**Deployments** → 점 3개 메뉴 → **Restart**. 볼륨의 `config.json` 은 유지됨.

### 비용 모니터링
**Usage** 탭에서 월 누적 크레딧 사용량 확인. $5 도달 시 자동 멈춤 (요금 폭탄 방지).

### 토큰 회전
Developer Portal 에서 토큰 reset → Railway Variables 의 `DISCORD_TOKEN` 갱신 → 자동 재배포로 즉시 반영.

---

## 7. 트러블슈팅

| 증상 | 원인 | 조치 |
|------|------|------|
| 빌드 실패: `apt-get` 에러 | base 이미지 일시 장애 | Redeploy |
| 시작 직후 crash + `RuntimeError: FFmpeg` | Dockerfile 의 `ffmpeg` 설치 단계 실패 | 빌드 로그 확인 |
| `synced 0 slash commands` 만 보임 | 이전 cog 가 로드 안 됨 | Variables 의 `DISCORD_TOKEN` 유효성 점검 |
| 슬래시 명령이 Discord 에서 안 보임 | 전역 sync 캐시 1시간 | `TEST_GUILD_ID` 추가 후 재배포하면 즉시 반영 |
| 재배포 후 guild 설정 사라짐 | Volume 미마운트 또는 `CONFIG_PATH` 미설정 | §2, §3 확인 |
| `Privileged Intents` 에러로 게이트웨이 거부 | Developer Portal 의 Intents 미활성 | Server Members + Message Content 둘 다 ON |

---

## 8. 다른 호스팅과의 비교

| 항목 | Railway | Oracle Cloud Free | 본인 PC |
|------|---------|-------------------|---------|
| 초기 세팅 난이도 | ⭐ 쉬움 | ⭐⭐⭐ 중간 | ⭐ 쉬움 |
| 24h 운영 | ✅ | ✅ | ❌ (PC 켜야 함) |
| 자동 재배포 | ✅ git push | ❌ 수동 | – |
| 월 비용 | ~$2–3 (무료 크레딧 내) | 평생 무료 | 전기료 |
| 추천 사용자 | 처음 배포 / 빠른 시작 | 무료로 본격 운영 | 테스트 / 본인만 사용 |
