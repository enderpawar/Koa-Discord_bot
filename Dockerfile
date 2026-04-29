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

# Railway Volume 이 /data 에 마운트된다고 가정. 미마운트여도 충돌 없음.
ENV CONFIG_PATH=/data/config.json \
    PYTHONUNBUFFERED=1

CMD ["python", "bot.py"]
