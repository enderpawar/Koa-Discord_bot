# Oracle Cloud 배포 가이드

본 문서는 Oracle Cloud Infrastructure(OCI) 의 **Always Free** ARM 인스턴스에서 본 Discord TTS 봇을 24시간 운영하는 단계별 절차입니다.

> 예상 비용: Always Free 한도 내에서 무료. 단 아래 §0 의 제약을 먼저 확인할 것.

---

## 0. 시작 전에 알아야 할 것

Always Free 는 **기간** 제한이 없을 뿐, **사양과 정책은 예고 없이 바뀝니다.**

- **2026-06-15** 자로 Ampere A1 무료 한도가 4 OCPU/24GB → **2 OCPU/12GB** 로 축소됐습니다 (공지 없이 시행). 본 봇에는 2 OCPU/12GB 로도 충분합니다.
- **유휴 인스턴스 회수**: 7일 기준 **CPU 사용률 AND 네트워크 사용률 AND(멀티 OCPU 샤드는) 메모리 사용률** 이 95th percentile 로 모두 20% 미만이어야 회수 대상입니다 — 세 지표가 **모두** 낮아야 유휴로 판정되는 AND 조건입니다. **TTS 봇은 CPU/네트워크가 대부분 유휴라 그대로 두면 회수 대상입니다.**
  → **대응: 메모리 사용률만 20% 이상으로 유지.** 카드 등록·PAYG 전환 없이 Always Free 그대로, 이미 할당된 12GB 중 일부를 상시 점유해 세 조건 중 하나를 깨뜨리는 방식. 본 저장소의 `docker-compose.yml` 에 포함된 `mem-anchor` 서비스(`scripts/mem_anchor.py`)가 이를 자동 수행합니다 — 별도 설정 불필요, `docker compose up -d` 만으로 같이 뜸.
  - 전제조건: 인스턴스의 **Oracle Cloud Agent → Compute Instance Monitoring** 플러그인이 Running 상태여야 사용률 지표 자체가 수집됩니다 (§8 에서 확인).
  - 카드 등록 자체를 꺼리지 않는다면 PAYG 업그레이드도 대안입니다: Always Free 한도 내 사용은 계속 무료이고 회수 리스크가 완전히 사라지지만, 초과 리소스를 실수로 만들면 과금될 수 있습니다 — 이 경우 OCI Billing → Budgets 에서 $1 이하 알림을 걸어두세요.
- **용량 확보**: 인기 리전은 `Out of host capacity` 가 상시 발생합니다. 한국 사용자는 서울/춘천이 특히 어렵고, 일본(도쿄/오사카) 이 차선입니다. 며칠 걸릴 수 있으니 감안하세요.
- **계정 정지 리스크**: 사전 통보 없는 무료 계정 정지 사례가 보고됩니다. §7 의 백업을 반드시 설정하세요.

---

## 1. 사전 준비

- [ ] Oracle Cloud 계정 생성 (Always Free 그대로 유지 — §0 의 메모리 앵커로 회수 방지)
- [ ] Discord Developer Portal 의 봇 토큰
- [ ] Azure Speech 리소스의 키와 리전
- [ ] Discord Developer Portal → Bot → **Privileged Gateway Intents**: `SERVER MEMBERS` + `MESSAGE CONTENT` 둘 다 ON

---

## 2. 인스턴스 생성

1. OCI 콘솔 → **Compute** → **Instances** → **Create Instance**
2. **Image and shape** → **Change shape**:
   - Shape series: **Ampere**
   - Shape: `VM.Standard.A1.Flex`
   - OCPU **2**, Memory **12 GB** (현재 Always Free 한도)
3. **Image**: Ubuntu 22.04 또는 24.04 (ARM64 빌드)
4. **Add SSH keys** → 공개키 업로드 또는 생성한 키 다운로드
5. **Create**

> `Out of host capacity` 가 뜨면 다른 가용 도메인(AD) 또는 리전으로 재시도하세요.

---

## 3. 서버 기본 설정

SSH 접속 후:

```bash
ssh -i <개인키> ubuntu@<인스턴스_공인IP>

# Docker 설치
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER
newgrp docker   # 또는 재로그인
```

Docker 부팅 시 자동 시작:

```bash
sudo systemctl enable --now docker
```

---

## 4. 코드 배포

```bash
git clone https://github.com/enderpawar/Koa_bot.git
cd Koa_bot
cp .env.example .env
nano .env        # §5 참고해 값 입력
mkdir -p data    # 볼륨 마운트 지점
```

**기존 배포에서 백업해 둔 `config.json` 이 있다면 지금 복원합니다:**

```bash
# 로컬에서 서버로 전송
scp -i <개인키> config.json ubuntu@<공인IP>:~/Koa_bot/data/config.json
```

빌드 및 기동:

```bash
docker compose up -d --build
```

첫 빌드는 ARM 에서 5~10분 걸립니다.

---

## 5. 환경변수 (`.env`)

| Key | 필수 | 값 |
|-----|------|-----|
| `DISCORD_TOKEN` | ✅ | Developer Portal 의 봇 토큰 |
| `AZURE_SPEECH_KEY` | ✅ | Azure Speech 리소스 키 |
| `AZURE_SPEECH_REGION` | ✅ | 예: `koreacentral` |
| `LOG_LEVEL` | – | `INFO` (기본 권장) |
| `TEST_GUILD_ID` | – | 슬래시 명령 즉시 sync 할 길드 ID. 미설정 시 전역 sync (캐시 1시간) |
| `ADMIN_WEB_ENABLED` | – | compose 가 `1` 로 못박음. 끄고 싶을 때만 빈 값으로 덮어씀 (§6) |
| `ADMIN_LOGIN_DB_PATH` | – | 일회용 로그인 해시 DB 경로. Docker 기본값 `/data/admin_login.sqlite3` |
| `ADMIN_WEB_HOST` | – | compose 가 `0.0.0.0` 으로 못박음. compose 없이 띄울 때만 직접 지정 (§6) |
| `ADMIN_WEB_PUBLIC_URL` | – | `http://<공인IP>:8080` — 봇이 안내하는 대시보드 주소 |
| `MC_WHITELIST_SSH_*` | – | `/마크 화이트리스트 등록` 사용 시 필요 — 아래 절 참조 |

> ⚠️ `DISCORD_TOKEN` 과 `AZURE_SPEECH_KEY` 는 절대 저장소에 커밋하지 마세요. `.env` 는 `.gitignore` 대상입니다.

`CONFIG_PATH` / `RANK_PATH` 는 `Dockerfile` 의 `ENV` 가 `/data` 볼륨을 가리키므로 `.env` 에서 생략합니다.

### Minecraft 화이트리스트 SSH

> ⚠️ **현재 비활성화됨.** `/마크` 와 `/클라우드` 는 `bot.py` 의 KNOWN_EXTENSIONS 에서
> 빠져 있어 로드되지 않습니다. 두 기능 모두 운영자 개인 GCP VM 한 대를 대상으로 하는데
> `default_permissions=None` 이라 코아를 초대한 아무 서버의 아무나 쓸 수 있었고, 전역
> 배포에서는 남의 서버 멤버가 VM 전원을 내리거나 화이트리스트에 자신을 올릴 수
> 있습니다. 되살리려면 길드 허용 목록으로 먼저 가두세요. 아래 절차와 환경변수는
> 그때를 위해 남겨 둡니다.

코아는 Oracle에서 실행되고 Minecraft 서버는 GCP에 있으므로 로컬
`whitelist.json`을 수정하지 않습니다. GCP의 `mc-whitelist.sh`를 전용 SSH
키로 호출합니다. 이 키에는 셸 권한을 주지 말고 반드시 GCP 서버의
`authorized_keys`에서 source IP, `restrict`, forced-command를 모두 적용하세요.

1. Oracle 봇 호스트에서 전용 키를 만듭니다.

```bash
mkdir -p ~/koa-bot/shared/ssh
ssh-keygen -t ed25519 -N '' -C koa-bot-whitelist \
  -f ~/koa-bot/shared/ssh/mc-whitelist
cat ~/koa-bot/shared/ssh/mc-whitelist.pub
```

2. `cobblemon-server/gcp/mc-whitelist.sh`와
   `mc-whitelist-discord.sh`를 GCP VM의 `/usr/local/bin/`에 root 소유,
   mode `0755`로 설치합니다. `mc-whitelist-bot` 계정과 다음 sudoers 항목도
   만듭니다.

```text
mc-whitelist-bot ALL=(root) NOPASSWD: /usr/local/bin/mc-whitelist.sh add *
```

3. GCP VM의 `/home/mc-whitelist-bot/.ssh/authorized_keys`에 1번의 공개키를
   다음 형식으로 한 줄 추가합니다. `<ORACLE_PUBLIC_IP>`는 `/32` 없이 IP만
   적습니다.

```text
from="<ORACLE_PUBLIC_IP>",restrict,command="/usr/local/bin/mc-whitelist-discord.sh" ssh-ed25519 AAAA... koa-bot-whitelist
```

4. GCP VM에서 신뢰할 호스트 공개키를 읽습니다. `ssh-keyscan` 결과를 그대로
   믿지 말고 이미 인증된 관리 SSH 세션에서 읽어야 합니다.

```bash
sudo cat /etc/ssh/ssh_host_ed25519_key.pub
```

5. Oracle의 `.env`에 값을 넣습니다.

```bash
base64 -w0 ~/koa-bot/shared/ssh/mc-whitelist
```

```ini
MC_WHITELIST_SSH_HOST=<GCP_MC_PUBLIC_IP>
MC_WHITELIST_SSH_PORT=22
MC_WHITELIST_SSH_USER=mc-whitelist-bot
MC_WHITELIST_SSH_PRIVATE_KEY_B64=<위 base64 출력>
MC_WHITELIST_SSH_HOST_KEY=ssh-ed25519 AAAA...
```

GCP 방화벽의 22번 포트도 가능하면 Oracle 공인 IP `/32`에서만 접근하도록
제한하세요. RCON 25575는 계속 외부에 열지 않습니다.

---

## 6. 웹 어드민을 쓸 경우 (선택)

`docker-compose.yml` 로 띄우면 **따로 설정할 것이 없습니다.** `ADMIN_WEB_ENABLED=1` 과
`ADMIN_WEB_HOST=0.0.0.0` 은 이 compose 구성의 상수라서 compose 가 기본값으로 못박습니다.
cloudflared 가 별도 컨테이너이므로 봇은 `0.0.0.0` 에 바인딩해야 하고(`127.0.0.1` 이면
컨테이너 루프백에만 묶여 터널이 못 붙습니다), 이 파일로 띄운다는 것 자체가 웹 어드민을
쓴다는 뜻입니다. 두 값을 배포 환경 변수에 의존하게 두면 하나만 빠져도 터널이 502 만
뱉는 형태로 조용히 깨집니다.

바꿔야 할 때만 `.env` 에 적으면 compose 기본값을 덮어씁니다:

```
# 웹 어드민을 끄고 싶을 때
ADMIN_WEB_ENABLED=
# 포트를 바꿀 때
ADMIN_WEB_PORT=8080
# 도메인 + named tunnel 로 옮겨 고정 주소를 쓸 때
ADMIN_WEB_PUBLIC_URL=https://<고정 주소>
```

compose 없이 직접 `python bot.py` 로 띄우는 경우에만 `ADMIN_WEB_HOST` 를 직접 지정해야
합니다. `cogs/web_admin_cog.py` 의 `_web_host()` 는 이 변수가 없으면 `127.0.0.1` 에
바인딩합니다 — 외부 공개는 호스팅 환경 추측이 아니라 명시적 설정으로만 이뤄집니다.

Cloudflare Tunnel을 사용하면 8080 인바운드 포트를 열지 마세요. `docker-compose.yml`은
호스트의 `127.0.0.1:8080`에만 게시하고, `cloudflared`가 Docker 내부의 `bot:8080`으로
아웃바운드 연결합니다. 따라서 OCI Security List와 인스턴스 방화벽에 8080 허용 규칙은
필요하지 않습니다.

웹 어드민을 쓰지 않으면 `ADMIN_WEB_ENABLED` 를 비워 두세요. 자동으로 비활성화되고 포트를 열 필요도 없습니다.

### 6.1 로그인 — Discord 일회용 권한

대시보드 접근 권한은 **서버 단위**입니다.

- 서버에서 `/관리자 대시보드`를 실행하면 Discord가 실행자의 현재 `관리자` 권한을 확인합니다.
- 응답은 실행자에게만 보이며, 서버 ID나 이름이 없는 5분짜리 일회용 링크를 제공합니다.
- 링크의 256비트 토큰은 URL fragment(`#token=...`)에 들어가므로 최초 HTTP 요청,
  Cloudflare 접근 로그, Referer에 전달되지 않습니다. 브라우저가 즉시 fragment를 지운 뒤
  HTTPS POST 본문으로 교환합니다.
- SQLite에는 토큰 원문이 아닌 sha256 해시만 저장됩니다. 교환은 원자적
  `DELETE ... RETURNING`으로 처리되어 동시에 제출해도 한 요청만 성공합니다.
- 로그인할 때와 설정 변경 때 Discord 관리자 권한을 다시 확인합니다. 세션과 모든 API는
  발급된 서버 ID를 서버 측에서만 사용하며 클라이언트가 보낸 서버 ID는 받지 않습니다.
- 고정 로그인 키, 전 서버 마스터 토큰, 길드 선택 UI는 없습니다.

> 일회용 링크 자체가 5분 동안의 로그인 권한입니다. Discord 응답은 비공개이지만 사용자가
> 링크를 복사해 다른 사람에게 넘기면 그 사람이 먼저 소비할 수 있으므로 공유하면 안 됩니다.

### 6.2 보안

어드민에 적용된 방어:

| 항목 | 동작 |
|---|---|
| 로그인 범위 | Discord에서 확인한 사용자 + 서버 하나에만 발급 |
| 권한 저장 | 5분짜리 토큰의 sha256 해시만 SQLite에 저장, 원문 미보관, 파일 권한 0600 |
| 원자성 | `BEGIN IMMEDIATE` + `DELETE ... RETURNING`; 일회용 토큰은 정확히 한 요청만 소비 |
| 은닉성 | 로그인 전 서버 ID·이름 미노출, 토큰은 URL fragment에서 즉시 제거 |
| 세션 | 로그인 성공 시 임의 세션 ID 발급. 쿠키에 일회용 토큰을 담지 않는다 |
| 세션 만료 | 15분 유휴 또는 발급 30분 후. 로그아웃하면 서버에서 즉시 폐기 |
| 쿠키 | `HttpOnly`, `SameSite=Strict`. HTTPS에서는 `Secure` + `__Host-` 접두사 자동 |
| 로그인 시도 | IP 당 5회 실패부터 잠금. 30초→1분→2분…최대 15분 지수 백오프 |
| 잠금 기록 | 만료분 정리 + 최대 4096개 상한 (분산 시도로 메모리 고갈 방지) |
| 쿼리스트링 인증 | 없음. `?token=` 은 접근 로그·Referer·히스토리에 남아 지원하지 않는다 |
| 마스터 우회 | `Authorization`, `X-Admin-Token`, 전 서버 세션을 지원하지 않음 |
| 변경 작업 | 세션의 서버만 사용하고 Discord 관리자 권한을 매번 재확인 |
| CSRF | `SameSite=Strict` + 상태 변경 요청의 `Sec-Fetch-Site` 검사 (login CSRF 포함) |
| CSP | `default-src 'none'`, 인라인 style/script 는 요청별 nonce 로만 허용 |
| 기타 헤더 | `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` |
| HSTS | https 로 서비스할 때만 자동 (`max-age=31536000`). 평문에는 붙이지 않는다 |

> ⚠️ **평문 HTTP 로 공개하면 일회용 토큰 교환과 세션 쿠키가 노출됩니다.** 위 방어는
> 전송 구간을 보호하지 못합니다. 공개 운영이라면 Cloudflare Tunnel 또는 Caddy·nginx로 TLS 를 붙이고
> `ADMIN_WEB_PUBLIC_URL=https://...` 로 지정하세요 (`Secure` 쿠키가 자동으로 켜집니다).
> 프록시가 TLS 를 끊고 봇에는 평문으로 전달하는 구성이라면 `ADMIN_WEB_COOKIE_SECURE=1`
> 을 직접 켜세요. TLS 없이 쓸 거라면 `ADMIN_WEB_HOST` 를 열지 말고 SSH 터널
> (`ssh -L 8080:127.0.0.1:8080 ubuntu@<IP>`)로 접속하는 편이 안전합니다.

봇 기동 시 평문 HTTP 로 외부에 열려 있으면 로그에 경고가 남습니다.

### 로그인 남용 집계와 신뢰 프록시

로그인 실패는 출발지 IP 별로 셉니다. Cloudflare Tunnel 뒤에서는 봇이 보는 IP 가
모든 사용자에게 `cloudflared` 컨테이너 하나로 같아지므로, 실제 사용자 IP 로
집계하려면 `ADMIN_WEB_TRUSTED_PROXIES` 에 신뢰할 직전 홉을 IP 또는 CIDR 로
등록해야 합니다 (`docker-compose.yml` 이 `172.16.0.0/12` 를 넣어 둡니다).
등록하지 않으면 `CF-Connecting-IP` 헤더를 아예 무시합니다 — 위조된 헤더로
집계를 흐리는 것보다 안전합니다.

> 이 집계는 **차단이 아니라 남용 신호** 용도입니다. 유효한 일회용 링크를 든
> 관리자는 같은 출발지가 잠금 상태여도 항상 로그인할 수 있습니다. 토큰이
> 256비트 난수 + 1회용이라 무차별 대입이 성립하지 않는 반면, 먼저 거부하는
> 구조에서는 공용 IP 하나로 다른 서버 관리자까지 묶여 버리기 때문입니다.

---

## 7. 백업 — 반드시 설정할 것

`data/` 에 `config.json`, `rank_stats.json`, 파티 모집 상태를 담은 `party.db`가
들어 있습니다. 계정 정지나 인스턴스 손실에 대비해 주기적으로 서버 밖으로 빼내세요.

간단한 cron 예시 (매일 04:00, 최근 14개 보관):

```bash
mkdir -p ~/backups
crontab -e
```

```cron
0 4 * * * cd ~/Koa_bot && tar czf ~/backups/data-$(date +\%F).tar.gz data/ && ls -1t ~/backups/data-*.tar.gz | tail -n +15 | xargs -r rm
```

이것만으로는 인스턴스가 사라지면 백업도 같이 사라집니다. **로컬 PC 로 주기적으로 내려받거나** OCI Object Storage 로 업로드하세요:

```bash
# 로컬에서 실행
scp -i <개인키> ubuntu@<공인IP>:~/backups/data-*.tar.gz ./backups/
```

---

## 8. 배포 확인

```bash
docker compose logs -f
```

다음 로그가 보이면 성공:

```
logged in as <봇이름> (id=...)
synced N slash commands (global)
loaded extension: cogs.tts_cog
```

Discord 서버 멤버 목록에서 봇 아이콘이 **초록색(online)** 인지 확인합니다.

**유휴 회수 방지 확인** (Always Free 유지 시):

```bash
docker stats --no-stream mem-anchor
```
`MEM USAGE` 가 `~2.6GB` 근처인지 확인합니다. 이어서 OCI 콘솔 → 인스턴스 상세 →
**Oracle Cloud Agent** 탭에서 **Compute Instance Monitoring** 플러그인이
**Running** 상태인지 확인하세요 — 꺼져 있으면 메모리 앵커를 띄워도 사용률 지표
자체가 Oracle 쪽에 집계되지 않아 무의미합니다.

---

## 9. GitHub Actions 자동 재배포

저장소의 `.github/workflows/deploy-oracle.yml` 은 `main` 브랜치 push 또는
GitHub Actions 화면의 수동 실행으로 새 릴리스를 전송하고, Docker 이미지를
재빌드한 뒤 Discord 로그인과 메모리 앵커 구동까지 확인합니다. 서버의 영구
데이터는 `~/koa-bot/shared/data`, 환경변수는
`~/koa-bot/shared/.env` 에 보존됩니다.

GitHub 저장소 **Settings → Secrets and variables → Actions** 에 다음 값을
등록합니다.

| 종류 | 이름 | 값 |
|------|------|----|
| Secret | `OCI_HOST` | 인스턴스 공인 IP 또는 DNS 이름 |
| Secret | `OCI_USER` | Ubuntu 이미지라면 `ubuntu` |
| Secret | `OCI_SSH_PRIVATE_KEY` | 인스턴스 SSH 개인키 전체 |
| Secret | `OCI_SSH_KNOWN_HOSTS` | `ssh-keyscan -H <공인IP>` 결과를 별도 신뢰 경로에서 지문 확인 후 등록 |
| Secret | `DISCORD_TOKEN` | Discord 봇 토큰 |
| Secret | `AZURE_SPEECH_KEY` | Azure Speech 키 |
| Secret | `AZURE_SPEECH_REGION` | Azure Speech 리전 |
| Secret | `TEST_GUILD_ID` | 선택: 즉시 슬래시 명령 동기화 대상 |
| Variable | `LOG_LEVEL` | 선택: 기본 `INFO` |
| Variable | `ADMIN_WEB_ENABLED` | 불필요 — compose 가 `1` 로 못박음 (§6) |
| Variable | `ADMIN_WEB_HOST` | 불필요 — compose 가 `0.0.0.0` 으로 못박음 (§6) |
| Variable | `ADMIN_WEB_PORT` | 선택: 기본 `8080` |
| Variable | `ADMIN_WEB_PUBLIC_URL` | 선택: 외부 대시보드 URL |
| Variable | `MEM_ANCHOR_BYTES` | 선택: 기본 `2600000000` |
| Variable | `MEM_ANCHOR_INTERVAL_SEC` | 선택: 기본 `30` |
| Variable | `MC_WHITELIST_SSH_HOST` | GCP Minecraft VM 공인 IP |
| Variable | `MC_WHITELIST_SSH_PORT` | 기본 `22` |
| Variable | `MC_WHITELIST_SSH_USER` | `mc-whitelist-bot` |
| Secret | `MC_WHITELIST_SSH_PRIVATE_KEY_B64` | 전용 SSH 개인키의 base64 |
| Variable | `MC_WHITELIST_SSH_HOST_KEY` | GCP VM의 고정 SSH 호스트 공개키 |

`OCI_SSH_KNOWN_HOSTS` 는 Actions 실행 중 즉석에서 수집하지 않습니다.
서버 지문을 미리 확인한 값을 고정해야 중간자 공격으로 다른 서버에 토큰을
전송하는 일을 막을 수 있습니다.

최초 실행도 별도 수동 설치 없이 가능합니다. 배포 스크립트가 Docker와
Compose를 설치하고 부팅 시 Docker 자동 시작을 활성화합니다. 단 `OCI_USER` 는
비밀번호 없이 `sudo apt-get` 과 `sudo systemctl` 을 실행할 수 있어야 합니다
(기본 Ubuntu OCI 사용자는 해당).

---

## 10. 운영

### 코드 업데이트
GitHub Actions를 설정했다면 `main` push마다 자동 재배포됩니다. 서버에서
직접 수동 배포하려면:

```bash
cd ~/Koa_bot
git pull
docker compose up -d --build
```

### 재시작 / 중지
```bash
docker compose restart
docker compose down
```
`data/` 는 bind mount 이므로 컨테이너를 지워도 설정, 랭킹, 파티 모집 상태는 남습니다.

### 로그
```bash
docker compose logs -f --tail 100
```
`LOG_LEVEL=DEBUG` 로 바꾸고 `docker compose up -d` 하면 상세 게이트웨이 로그까지 출력됩니다. 로그는 10MB × 3 파일로 자동 로테이션됩니다 (`docker-compose.yml`).

### 토큰 회전
`.env` 의 `DISCORD_TOKEN` 수정 → `docker compose up -d` (재빌드 불필요).

---

## 11. 트러블슈팅

| 증상 | 원인 | 조치 |
|------|------|------|
| 인스턴스 생성 시 `Out of host capacity` | 리전 ARM 용량 소진 | 다른 AD/리전으로 재시도. 며칠 걸릴 수 있음 |
| 인스턴스가 어느 날 사라짐 | 유휴 회수 정책, `mem-anchor` 미기동 상태로 방치됨 | §0/§8 확인 — 그래도 불안하면 PAYG 업그레이드 (§0) |
| 시작 직후 crash + `RuntimeError: FFmpeg` | 이미지 빌드 실패 | `docker compose build --no-cache` |
| 웹 어드민에 접속 불가 | `ADMIN_WEB_HOST` 미설정 또는 방화벽 | §6 의 두 방화벽 + `0.0.0.0` 확인 |
| 재시작 후 guild 설정 사라짐 | `data/` 미생성 또는 볼륨 미마운트 | `mkdir -p data` 후 `docker compose up -d` |
| 재시작 후 랭킹만 사라짐 | `RANK_PATH` 미설정 (구버전 Dockerfile) | 최신 `Dockerfile` 로 재빌드 |
| 슬래시 명령이 Discord 에서 안 보임 | 전역 sync 캐시 1시간 | `TEST_GUILD_ID` 추가 후 재시작하면 즉시 반영 |
| `Privileged Intents` 에러로 게이트웨이 거부 | Developer Portal 의 Intents 미활성 | Server Members + Message Content 둘 다 ON |
| 음성은 되는데 소리가 안 남 | `libopus0` 누락 | 이미지 재빌드 |
| 며칠 뒤 인스턴스가 사라짐 (Always Free 유지 중) | `mem-anchor` 컨테이너가 안 뜸 / Cloud Agent 모니터링 플러그인 꺼짐 | `docker ps` 로 `mem-anchor` Up 상태 확인, OCI 콘솔에서 Compute Instance Monitoring 플러그인 Running 확인 |
| 인스턴스 메모리가 부족해 보임 (`free -h`) | `mem-anchor` 가 2.6GB 점유 중 (의도된 동작) | 문제 없음. 봇 자체 메모리 사용량이 크다면 `docker-compose.yml` 의 `MEM_ANCHOR_BYTES` 를 낮춰 여유 확보 (단 12GB 의 20%=2.4GB 이상은 유지) |

---

## 12. 다른 호스트에서 이전할 때 체크리스트

- [ ] 기존 배포의 `config.json` 백업
- [ ] `.env` 에 `AZURE_SPEECH_KEY` / `AZURE_SPEECH_REGION` 포함
- [ ] 웹 어드민은 compose 기본값으로 켜진다. 고정 주소를 쓸 때만 `ADMIN_WEB_PUBLIC_URL` 추가 (§6)
- [ ] `data/config.json` 복원 후 `docker compose up -d --build`
- [ ] Discord 에서 `/상태`로 길드 설정이 살아있는지 확인
- [ ] 백업 cron 설정 (§7)
