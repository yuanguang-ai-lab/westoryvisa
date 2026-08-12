#!/bin/zsh
set -e
umask 077

cd "$(dirname "$0")"

# Keep uv's Python runtime and package cache inside this project. This avoids
# permission problems in a locked-down macOS home directory and keeps the
# LibreTranslate installation self-contained.
mkdir -p data/uv-cache data/uv-python
export UV_CACHE_DIR="$PWD/data/uv-cache"
export UV_PYTHON_INSTALL_DIR="$PWD/data/uv-python"

UV_BIN="$(command -v uv 2>/dev/null || true)"
if [ -z "$UV_BIN" ]; then
  echo "没有找到 uv，正在打开免费安装说明。安装 uv 后重新双击本脚本。"
  open "https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
fi

if [ ! -x ".venv-libretranslate/bin/python" ]; then
  if [ -x ".venv-docling/bin/python" ] && \
    .venv-docling/bin/python -c 'import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] < (3, 14) else 1)' \
      >/dev/null 2>&1; then
    echo "正在复用文档扫描环境的 Python 3.11 创建独立翻译环境..."
    .venv-docling/bin/python -m venv .venv-libretranslate
  else
    echo "正在准备独立的 Python 3.12 环境，避免与系统 Python 3.14 冲突..."
    "$UV_BIN" python install 3.12
    "$UV_BIN" venv --python 3.12 .venv-libretranslate
  fi
fi

echo "正在安装 LibreTranslate 1.9.6 与中英模型依赖..."
"$UV_BIN" pip install --python .venv-libretranslate/bin/python "libretranslate==1.9.6"

python3 - <<'PY'
import re
import shlex
from pathlib import Path

path = Path(".env")
updates = {
    "DS160_TRANSLATION_PROVIDER": "auto",
    "LIBRETRANSLATE_URL": "http://127.0.0.1:5000",
    "SCHOOL_LOOKUP_PROVIDER": "nominatim",
}
assignment = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
preserved = [
    line for line in lines
    if not (assignment.match(line) and assignment.match(line).group(1) in updates)
]
while preserved and not preserved[-1].strip():
    preserved.pop()
if preserved:
    preserved.append("")
preserved.append("# DocFlow local translation and institution lookup")
for key, value in updates.items():
    preserved.append(f"export {key}={shlex.quote(value)}")
path.write_text("\n".join(preserved) + "\n", encoding="utf-8")
path.chmod(0o600)
PY

mkdir -p data
if ! curl -fsS --max-time 2 "http://127.0.0.1:5000/languages" >/dev/null 2>&1; then
  echo "正在首次启动 LibreTranslate，只加载中文和英文模型..."
  nohup .venv-libretranslate/bin/libretranslate \
    --host 127.0.0.1 --port 5000 --load-only zh,en --disable-web-ui \
    > data/libretranslate.log 2>&1 &
fi

for _ in {1..120}; do
  curl -fsS --max-time 2 "http://127.0.0.1:5000/languages" >/dev/null 2>&1 && break
  sleep 1
done

if ! curl -fsS --max-time 2 "http://127.0.0.1:5000/languages" >/dev/null 2>&1; then
  echo "LibreTranslate 尚未就绪。请查看 data/libretranslate.log。"
  exit 1
fi

RESULT="$(curl -fsS --max-time 30 -X POST "http://127.0.0.1:5000/translate" \
  -H "Content-Type: application/json" \
  --data '{"q":"我在学校学习财务管理","source":"zh","target":"en","format":"text"}')"
if [[ "$RESULT" != *"translatedText"* ]]; then
  echo "服务已启动，但中译英测试未通过。请查看 data/libretranslate.log。"
  exit 1
fi

echo ""
echo "LibreTranslate 中译英已启用并通过测试。"
echo "请关闭旧 DocFlow 终端，再双击“启动完整版本.command”。"
