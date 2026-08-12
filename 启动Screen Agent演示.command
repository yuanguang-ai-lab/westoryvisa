#!/bin/zsh
set -e
umask 077

cd "$(dirname "$0")"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "当前 DocFlow + Chrome 演示入口仅支持 macOS。"
  read -k 1 "?按任意键退出..."
  exit 1
fi

if [ ! -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]; then
  echo "没有找到 Google Chrome，请先安装后重试。"
  read -k 1 "?按任意键退出..."
  exit 1
fi

if [ -f "data/email_settings.env" ]; then
  source "data/email_settings.env"
fi
if [ -f ".env" ]; then
  source ".env"
fi

PORT=4275
while lsof -nP -iTCP:${PORT} -sTCP:LISTEN >/dev/null 2>&1; do
  PORT=$((PORT + 1))
done

echo ""
echo "DocFlow Chrome 逐页填写演示：http://127.0.0.1:${PORT}"
echo ""
echo "使用方式："
echo "1. 首次使用请双击“安装DocFlow Chrome扩展.command”并完成 Chrome 确认。"
echo "2. 在 Chrome 中恢复正确的 CEAC 申请会话。"
echo "3. 在 DocFlow 的 DS-160 初稿中打开“Chrome 逐页填写”。"
echo "4. 点击“连接 Chrome 并开始”。"
echo "5. 普通页面写入并校验后可自动 Next；敏感或未映射页面会暂停。"
echo ""
echo "不再需要 Codex 插件，也不再复制一次性任务令牌。"
echo "关闭这个窗口会停止 DocFlow 本地服务。"
echo ""

(sleep 1.2; open -a "Google Chrome" "http://127.0.0.1:${PORT}") &
python3 -m scripts.run_local "$PORT"
