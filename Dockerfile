FROM python:3.13-slim

# discord.py 음성 송출에 필요한 시스템 의존성: FFmpeg + libopus 헤더
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libopus0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 레이어 캐시 최적화: requirements 먼저 복사
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 영구 볼륨이 /data 에 마운트된다고 가정. 미마운트여도 충돌 없음(컨테이너 FS 에 기록).
# 데이터 경로를 모두 /data 로 지정해 재배포 후에도 설정과 사용자 데이터가 보존된다.
ENV CONFIG_PATH=/data/config.json \
    ADMIN_LOGIN_DB_PATH=/data/admin_login.sqlite3 \
    RANK_PATH=/data/rank_stats.json \
    PARTY_DB_PATH=/data/party.db \
    VALORANT_STORE_PATH=/data/valorant_ids.json \
    LOL_STORE_PATH=/data/lol_ids.json \
    PYTHONUNBUFFERED=1

CMD ["python", "bot.py"]
