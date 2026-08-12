#!/bin/zsh

set -u

cd -- "$(dirname -- "$0")"

PORT="${PORT:-4188}"
while /usr/sbin/lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; do
  PORT=$((PORT + 1))
done

printf '\nVisa Form Practice Lab\n'
printf '非官方网站，仅供虚构资料练习。\n'
printf '正在启动：http://127.0.0.1:%s/\n\n' "$PORT"

node scripts/dev-server.mjs . "$PORT" &
SERVER_PID=$!

cleanup() {
  kill "$SERVER_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

for _ in {1..30}; do
  if /usr/bin/curl -fsS "http://127.0.0.1:$PORT/" >/dev/null 2>&1; then
    /usr/bin/open "http://127.0.0.1:$PORT/"
    break
  fi
  sleep 0.2
done

wait "$SERVER_PID"
