#!/bin/zsh
cd "$(dirname "$0")"
if [ -f "data/email_settings.env" ]; then
  source "data/email_settings.env"
fi
if [ -f ".env" ]; then
  source ".env"
fi

PROJECT_DIR="$(pwd)"

stop_existing_docflow_servers() {
  local port pid process_cwd command attempt
  for port in {4175..4210}; do
    for pid in $(lsof -tiTCP:${port} -sTCP:LISTEN 2>/dev/null || true); do
      process_cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)"
      command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
      if [[ "$process_cwd" = "$PROJECT_DIR" ]] && \
        [[ "$command" == *"server.py"* || "$command" == *"backend.main"* || \
           "$command" == *"backend.api_main"* || "$command" == *"frontend.dev_server"* || \
           "$command" == *"scripts.run_local"* ]]; then
        echo "正在停止旧的 DocFlow 服务（端口 ${port}）..."
        kill "$pid" >/dev/null 2>&1 || true
        for attempt in {1..20}; do
          kill -0 "$pid" >/dev/null 2>&1 || break
          sleep 0.1
        done
      fi
    done
  done
  sleep 0.4
}

stop_existing_docflow_servers

echo "正在启动 DocFlow DS-160 数据库版..."
echo ""
PORT=4175
while lsof -nP -iTCP:${PORT} -sTCP:LISTEN >/dev/null 2>&1; do
  PORT=$((PORT + 1))
done

echo "网页地址：http://127.0.0.1:${PORT}"
echo "数据库文件：$(pwd)/data/docflow_ds160.sqlite3"
echo ""
if [ "$PORT" -ne 4175 ]; then
  echo "4175 已被占用，本次自动使用 ${PORT}。"
fi
echo "关闭这个窗口会停止本地服务器。"
echo ""
(sleep 1.2; open "http://127.0.0.1:${PORT}") &
python3 -m scripts.run_local "$PORT"
