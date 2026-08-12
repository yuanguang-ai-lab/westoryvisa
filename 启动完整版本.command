#!/bin/zsh
set -e
umask 077

cd "$(dirname "$0")"

if [ -f "data/email_settings.env" ]; then
  source "data/email_settings.env"
fi
if [ -f ".env" ]; then
  source ".env"
fi

PROJECT_DIR="$(pwd)"
export DS160_TRANSLATION_PROVIDER="${DS160_TRANSLATION_PROVIDER:-auto}"
export LIBRETRANSLATE_URL="${LIBRETRANSLATE_URL:-http://127.0.0.1:5000}"
export SCHOOL_LOOKUP_PROVIDER="${SCHOOL_LOOKUP_PROVIDER:-nominatim}"
LIBRETRANSLATE_PID=""

stop_existing_docflow_servers() {
  local port pid process_cwd attempt
  for port in {4175..4210}; do
    for pid in $(lsof -tiTCP:${port} -sTCP:LISTEN 2>/dev/null || true); do
      process_cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)"
      # The process command can be hidden in restricted shells. A listener whose
      # working directory is this project is enough to identify our old server.
      if [ "$process_cwd" = "$PROJECT_DIR" ]; then
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

docling_environment_ready() {
  [ -x ".venv-docling/bin/python" ] && \
  [ -x ".venv-docling/bin/docling-serve" ] && \
  .venv-docling/bin/python -c 'import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] < (3, 14) else 1)' \
    >/dev/null 2>&1
}

OCR_PROVIDER_NORMALIZED="${OCR_PROVIDER:-auto}"
OCR_PROVIDER_NORMALIZED="${OCR_PROVIDER_NORMALIZED:l}"
USE_MINERU=false
if [ "$OCR_PROVIDER_NORMALIZED" = "mineru" ] || \
   { [ "$OCR_PROVIDER_NORMALIZED" = "auto" ] && [ -n "${MINERU_API_TOKEN:-}" ]; }; then
  USE_MINERU=true
fi

if [ "$USE_MINERU" != "true" ] && ! docling_environment_ready; then
  echo "首次运行：尚未安装文档扫描环境，现在开始自动安装。"
  echo "安装会下载 Docling、RapidOCR 和模型依赖，可能需要几分钟。"
  echo ""
  DOCFLOW_NONINTERACTIVE=1 zsh "./安装文档扫描.command"
fi

stop_existing_docflow_servers

start_libretranslate() {
  local provider
  provider="${DS160_TRANSLATION_PROVIDER:-auto}"
  case "${provider:l}" in
    off|none|disabled|ollama) return ;;
  esac
  if curl -fsS --max-time 2 "${LIBRETRANSLATE_URL}/languages" >/dev/null 2>&1; then
    echo "LibreTranslate 中译英服务已连接。"
    return
  fi
  if [ ! -x ".venv-libretranslate/bin/libretranslate" ]; then
    echo "LibreTranslate 尚未安装；本次会回退到现有翻译能力。"
    echo "需要准确中译英时，双击“安装LibreTranslate.command”。"
    return
  fi
  echo "正在启动 LibreTranslate 中译英服务..."
  mkdir -p data
  .venv-libretranslate/bin/libretranslate \
    --host 127.0.0.1 --port 5000 --load-only zh,en --disable-web-ui \
    > data/libretranslate.log 2>&1 &
  LIBRETRANSLATE_PID=$!
  for _ in {1..40}; do
    curl -fsS --max-time 2 "${LIBRETRANSLATE_URL}/languages" >/dev/null 2>&1 && {
      echo "LibreTranslate 中译英服务已连接。"
      return
    }
    sleep 0.5
  done
  echo "LibreTranslate 暂未就绪；本次会回退到现有翻译能力。"
}

start_libretranslate

start_local_text_analysis() {
  local provider ollama_bin candidate
  provider="${DS160_TEXT_ANALYSIS_PROVIDER:-${DS160_TRANSLATION_PROVIDER:-auto}}"
  case "${provider:l}" in
    off|none|disabled|rules) return ;;
  esac
  if curl -fsS --max-time 2 "${OLLAMA_URL:-http://127.0.0.1:11434}/api/tags" >/dev/null 2>&1; then
    echo "本地文字识别与翻译服务已连接。"
    return
  fi
  ollama_bin="$(command -v ollama 2>/dev/null || true)"
  if [ -z "$ollama_bin" ]; then
    for candidate in \
      "/Applications/Ollama.app/Contents/Resources/ollama" \
      "$HOME/Applications/Ollama.app/Contents/Resources/ollama"; do
      if [ -x "$candidate" ]; then
        ollama_bin="$candidate"
        break
      fi
    done
  fi
  if [ -z "$ollama_bin" ]; then
    echo "本地语义模型尚未安装；顾问文字将使用增强规则识别。"
    echo "如需语义补漏与自由文本翻译，可双击“启用本地英文翻译.command”。"
    return
  fi
  echo "正在启动本地文字识别与翻译服务..."
  mkdir -p data
  if ! open -a Ollama >/dev/null 2>&1; then
    nohup "$ollama_bin" serve > data/ollama.log 2>&1 &
  fi
  for _ in {1..12}; do
    curl -fsS --max-time 2 "${OLLAMA_URL:-http://127.0.0.1:11434}/api/tags" >/dev/null 2>&1 && {
      echo "本地文字识别与翻译服务已连接。"
      return
    }
    sleep 0.5
  done
  echo "本地语义模型暂未就绪；本次先使用增强规则识别。"
}

start_local_text_analysis

PORT=4175
while lsof -nP -iTCP:${PORT} -sTCP:LISTEN >/dev/null 2>&1; do
  PORT=$((PORT + 1))
done

DOCLING_PID=""
cleanup() {
  if [ -n "$LIBRETRANSLATE_PID" ] && kill -0 "$LIBRETRANSLATE_PID" >/dev/null 2>&1; then
    kill "$LIBRETRANSLATE_PID" >/dev/null 2>&1
  fi
  if [ -n "$DOCLING_PID" ] && kill -0 "$DOCLING_PID" >/dev/null 2>&1; then
    kill "$DOCLING_PID" >/dev/null 2>&1
  fi
}
trap cleanup EXIT INT TERM

if [ "$USE_MINERU" = "true" ]; then
  echo "已选择 MinerU ${MINERU_MODEL_VERSION:-vlm} 精准解析；本次不启动本地 RapidOCR。"
else
  mkdir -p data
  if [ ! -s "data/docling_api_key" ]; then
    openssl rand -hex 32 > data/docling_api_key
  fi
  chmod 600 data/docling_api_key
  export DOCLING_SERVE_API_KEY="$(<data/docling_api_key)"

  if lsof -nP -iTCP:5001 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "检测到本地文档扫描服务已在 5001 端口运行，将直接使用。"
  else
    echo "正在后台启动 Docling Serve + RapidOCR..."
    UVICORN_HOST=127.0.0.1 \
    UVICORN_PORT=5001 \
    UVICORN_WORKERS=1 \
    DOCLING_SERVE_ENABLE_UI=true \
    DOCLING_SERVE_ENG_KIND=local \
    DOCLING_SERVE_ENG_LOC_NUM_WORKERS=1 \
    DOCLING_SERVE_MAX_SYNC_WAIT=300 \
    DOCLING_SERVE_API_KEY="$DOCLING_SERVE_API_KEY" \
      .venv-docling/bin/docling-serve run --enable-ui > data/docling-serve.log 2>&1 &
    DOCLING_PID=$!
  fi
fi

echo ""
echo "DocFlow 网页：http://127.0.0.1:${PORT}"
if [ "$USE_MINERU" = "true" ]; then
  echo "文档解析：MinerU ${MINERU_MODEL_VERSION:-vlm} 精准 API"
else
  echo "文档扫描：http://127.0.0.1:5001"
fi
echo "Computer Use 逐页填写：无需安装 Chrome 扩展。"
echo "进入正式表格后，点击 DocFlow 的交接按钮，再回到 Codex 发送已复制的短时指令。"
echo "Computer Use 会逐项复读并使用稳健节奏；验证码、敏感判断和最终提交由人工处理。"
echo "文档解析采用异步任务，处理时间取决于文件页数与服务队列。"
echo "关闭这个窗口会停止本次本地服务。"
echo ""

(sleep 1.2; open "http://127.0.0.1:${PORT}") &
python3 -m scripts.run_local "$PORT"
