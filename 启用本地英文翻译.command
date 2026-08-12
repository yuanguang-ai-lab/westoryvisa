#!/bin/zsh
set -e
umask 077

cd "$(dirname "$0")"

MODEL="${OLLAMA_MODEL:-qwen2.5:3b}"
OLLAMA_BIN="$(command -v ollama 2>/dev/null || true)"

if [ -z "$OLLAMA_BIN" ]; then
  for candidate in \
    "/Applications/Ollama.app/Contents/Resources/ollama" \
    "$HOME/Applications/Ollama.app/Contents/Resources/ollama"; do
    if [ -x "$candidate" ]; then
      OLLAMA_BIN="$candidate"
      break
    fi
  done
fi

if [ -z "$OLLAMA_BIN" ]; then
  echo "尚未安装免费的本地翻译引擎 Ollama。"
  echo "浏览器将打开官方 macOS 下载页；安装完成后，再双击本脚本。"
  open "https://ollama.com/download/mac"
  exit 1
fi

mkdir -p data
if ! curl -fsS --max-time 2 "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1; then
  echo "正在启动本地翻译服务..."
  open -a Ollama >/dev/null 2>&1 || true
  if ! curl -fsS --max-time 2 "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1; then
    nohup "$OLLAMA_BIN" serve > data/ollama.log 2>&1 &
  fi
  for _ in {1..30}; do
    curl -fsS --max-time 2 "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1 && break
    sleep 1
  done
fi

if ! curl -fsS --max-time 2 "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1; then
  echo "本地翻译服务未能启动。请先打开 Ollama 应用，再重新运行本脚本。"
  exit 1
fi

echo "正在准备中英翻译模型 ${MODEL}。首次下载需要一些时间和磁盘空间。"
"$OLLAMA_BIN" pull "$MODEL"

python3 - "$MODEL" <<'PY'
import re
import shlex
import sys
from pathlib import Path

path = Path(".env")
updates = {
    "DS160_TRANSLATION_PROVIDER": "auto",
    "DS160_TEXT_ANALYSIS_PROVIDER": "auto",
    "OLLAMA_URL": "http://127.0.0.1:11434",
    "OLLAMA_MODEL": sys.argv[1],
}
assignment = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
preserved = []
for line in lines:
    match = assignment.match(line)
    if match and match.group(1) in updates:
        continue
    preserved.append(line)
while preserved and not preserved[-1].strip():
    preserved.pop()
if preserved:
    preserved.append("")
preserved.append("# DocFlow local DS-160 translation")
for key, value in updates.items():
    preserved.append(f"export {key}={shlex.quote(value)}")
path.write_text("\n".join(preserved) + "\n", encoding="utf-8")
path.chmod(0o600)
PY

echo ""
echo "本地文字识别与英文翻译已启用。"
echo "请关闭旧的 DocFlow 终端窗口，再双击“启动完整版本.command”。"
echo "系统会从顾问粘贴的连续文字中补充识别字段，并升级学校、公司和说明文字。"
