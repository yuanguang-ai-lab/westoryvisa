#!/bin/zsh
set -e
umask 077

cd "$(dirname "$0")"

mkdir -p data
if [ ! -s "data/docling_api_key" ]; then
  openssl rand -hex 32 > data/docling_api_key
fi
chmod 600 data/docling_api_key

docling_environment_ready() {
  [ -x ".venv-docling/bin/python" ] && \
  [ -x ".venv-docling/bin/docling-serve" ] && \
  .venv-docling/bin/python -c 'import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] < (3, 14) else 1)' \
    >/dev/null 2>&1
}

if ! docling_environment_ready; then
  echo "首次运行：尚未安装文档扫描环境，现在开始自动安装。"
  DOCFLOW_NONINTERACTIVE=1 zsh "./安装文档扫描.command"
fi

export UVICORN_HOST=127.0.0.1
export UVICORN_PORT=5001
export UVICORN_WORKERS=1
export DOCLING_SERVE_ENABLE_UI=true
export DOCLING_SERVE_ENG_KIND=local
export DOCLING_SERVE_ENG_LOC_NUM_WORKERS=1
export DOCLING_SERVE_MAX_SYNC_WAIT=300
export DOCLING_SERVE_API_KEY="$(<data/docling_api_key)"

echo "正在启动本机文档扫描服务..."
echo "服务地址：http://127.0.0.1:5001"
echo "首次启动会下载并加载模型，可能需要几分钟。"
echo "关闭这个窗口会停止文档扫描。"
echo ""

exec .venv-docling/bin/docling-serve run --enable-ui
