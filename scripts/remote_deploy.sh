#!/usr/bin/env bash
set -Eeuo pipefail

release_id="${1:-}"
if [[ ! "$release_id" =~ ^[0-9a-f]{40}$ ]]; then
  echo "error: release id must be a 40-character Git commit SHA" >&2
  exit 2
fi

app_root="${HOME}/koa-bot"
release_dir="${app_root}/releases/${release_id}"
shared_dir="${app_root}/shared"
archive_path="/tmp/koa-bot-${release_id}.tar.gz"
env_path="/tmp/koa-bot-${release_id}.env"
rank_path="/tmp/koa-bot-${release_id}-rank-stats.json"
config_path="/tmp/koa-bot-${release_id}-config.json"

cleanup() {
  rm -f -- \
    "$archive_path" \
    "$env_path" \
    "$rank_path" \
    "$config_path" \
    "/tmp/koa-bot-${release_id}-deploy.sh"
}
trap cleanup EXIT

for required_file in "$archive_path" "$env_path"; do
  if [[ ! -f "$required_file" ]]; then
    echo "error: missing deployment artifact: $required_file" >&2
    exit 3
  fi
done

if ! command -v docker >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y docker.io docker-compose-v2
fi

if ! docker compose version >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y docker-compose-v2
fi

sudo systemctl enable --now docker

mem_total_kb="$(awk '/^MemTotal:/ { print $2 }' /proc/meminfo)"
if (( mem_total_kb < 2097152 )) && ! swapon --show=NAME --noheadings | grep -q .; then
  if [[ ! -f /swapfile ]]; then
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
  fi
  sudo swapon /swapfile
  if ! grep -q '^/swapfile ' /etc/fstab; then
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  fi
fi

docker_cmd=(docker)
if ! docker info >/dev/null 2>&1; then
  docker_cmd=(sudo docker)
fi

mkdir -p -- "$release_dir" "$shared_dir/data"
tar -xzf "$archive_path" -C "$release_dir"

install -m 600 "$env_path" "$shared_dir/.env"
if [[ -f "$rank_path" ]]; then
  install -m 600 "$rank_path" "$shared_dir/data/rank_stats.json"
fi
if [[ -f "$config_path" ]]; then
  install -m 600 "$config_path" "$shared_dir/data/config.json"
fi
ln -sfn "$shared_dir/.env" "$release_dir/.env"
ln -sfn "$shared_dir/data" "$release_dir/data"

compose=(
  "${docker_cmd[@]}" compose
  --project-name koa-bot
  --file "$release_dir/docker-compose.yml"
)

"${compose[@]}" config --quiet
anchor_enabled=false
if (( mem_total_kb >= 4194304 )); then
  anchor_enabled=true
  "${compose[@]}" up -d --build --remove-orphans
else
  # 메모리가 작은 인스턴스에서는 mem-anchor 를 빼고 띄운다. cloudflared 는
  # 128MB 남짓이라 함께 올린다 — 빼면 대시보드 공개 주소가 사라진다.
  "${compose[@]}" up -d --build --remove-orphans bot cloudflared
fi
ln -sfn "$release_dir" "$app_root/current"

deadline=$((SECONDS + 60))
while (( SECONDS < deadline )); do
  bot_state="$("${docker_cmd[@]}" inspect --format '{{.State.Status}}' koa-bot 2>/dev/null || true)"
  if [[ "$bot_state" == "running" ]] \
    && "${docker_cmd[@]}" logs koa-bot 2>&1 | grep -q "logged in as"; then
    break
  fi

  if [[ "$bot_state" == "exited" || "$bot_state" == "dead" ]]; then
    echo "error: bot container entered state: $bot_state" >&2
    "${docker_cmd[@]}" logs --tail 100 koa-bot >&2 || true
    exit 4
  fi
  sleep 3
done

if ! "${docker_cmd[@]}" logs koa-bot 2>&1 | grep -q "logged in as"; then
  echo "error: Discord login was not confirmed within 60 seconds" >&2
  "${docker_cmd[@]}" logs --tail 100 koa-bot >&2 || true
  exit 5
fi

if [[ "$anchor_enabled" == true ]]; then
  anchor_state="$("${docker_cmd[@]}" inspect --format '{{.State.Status}}' mem-anchor 2>/dev/null || true)"
  if [[ "$anchor_state" != "running" ]]; then
    echo "error: mem-anchor container is not running (state: ${anchor_state:-missing})" >&2
    "${docker_cmd[@]}" logs --tail 100 mem-anchor >&2 || true
    exit 6
  fi
fi

"${compose[@]}" ps
echo "deployment verified: ${release_id}"
