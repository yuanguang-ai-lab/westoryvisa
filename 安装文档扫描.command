#!/bin/zsh
set -e

cd "$(dirname "$0")"

echo "正在安装 DocFlow 文档扫描环境..."
echo "首次安装会下载 Docling、RapidOCR 和模型依赖，所需时间取决于网络速度。"
echo ""

is_compatible_python() {
  "$1" -c 'import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] < (3, 14) else 1)' \
    >/dev/null 2>&1
}

resolve_python() {
  local candidate="$1"
  if [[ "$candidate" == */* ]]; then
    [[ -x "$candidate" ]] && printf '%s\n' "$candidate"
  else
    command -v "$candidate" 2>/dev/null
  fi
}

REQUESTED_PYTHON="${DOCFLOW_PYTHON_BIN:-${PYTHON_BIN:-}}"
PYTHON_BIN=""
if [ -n "$REQUESTED_PYTHON" ]; then
  PYTHON_BIN="$(resolve_python "$REQUESTED_PYTHON" || true)"
  if [ -z "$PYTHON_BIN" ] || ! is_compatible_python "$PYTHON_BIN"; then
    echo "DOCFLOW_PYTHON_BIN 必须指向 Python 3.10 至 3.13。"
    exit 1
  fi
else
  PYTHON_CANDIDATES=(
    python3.13 python3.12 python3.11 python3.10
    /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3.10
    "$HOME/.local/bin/python3.13" "$HOME/.local/bin/python3.12" "$HOME/.local/bin/python3.11" "$HOME/.local/bin/python3.10"
  )
  for candidate in "${PYTHON_CANDIDATES[@]}"; do
    resolved="$(resolve_python "$candidate" || true)"
    if [ -n "$resolved" ] && is_compatible_python "$resolved"; then
      PYTHON_BIN="$resolved"
      break
    fi
  done
fi

if [ -z "$PYTHON_BIN" ]; then
  echo "没有找到兼容的 Python。Docling 当前请使用 Python 3.10 至 3.13，不要使用 3.14。"
  echo "可以运行：brew install python@3.12"
  exit 1
fi

SELECTED_VERSION="$($PYTHON_BIN -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "使用 $($PYTHON_BIN --version 2>&1)（$PYTHON_BIN）"

RECREATE_VENV=0
if [ -x ".venv-docling/bin/python" ]; then
  EXISTING_VERSION="$(.venv-docling/bin/python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
  if [ "$EXISTING_VERSION" != "$SELECTED_VERSION" ]; then
    RECREATE_VENV=1
    echo "检测到旧扫描环境使用 Python ${EXISTING_VERSION:-未知版本}，正在改用 Python ${SELECTED_VERSION} 重建。"
  fi
else
  RECREATE_VENV=1
fi

if [ "$RECREATE_VENV" = "1" ]; then
  "$PYTHON_BIN" -m venv --clear .venv-docling
fi
source .venv-docling/bin/activate
if ! python -m pip install --upgrade pip setuptools wheel; then
  echo ""
  echo "基础安装工具下载失败，请检查网络后重新双击“启动完整版本.command”。"
  exit 1
fi
if ! python -m pip install --prefer-binary --only-binary=grpcio -r requirements-ocr.txt; then
  echo ""
  echo "Docling 依赖安装失败。当前已使用兼容的 Python ${SELECTED_VERSION}，不会再本地编译 grpcio。"
  echo "请检查能否访问 pypi.org，然后重新运行安装。"
  exit 1
fi

python -c 'import grpc; print("grpcio", grpc.__version__)'
docling-serve --help >/dev/null

echo ""
echo "安装完成。以后请双击“启动完整版本.command”。"
if [ "${DOCFLOW_NONINTERACTIVE:-0}" != "1" ]; then
  echo "按任意键关闭窗口。"
  read -k 1
fi
