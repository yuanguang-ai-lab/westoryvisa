#!/bin/zsh
set -eu

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
EXTENSION_DIR="$ROOT_DIR/docflow-chrome-extension"

if [[ ! -f "$EXTENSION_DIR/manifest.json" ]]; then
  echo "没有找到 DocFlow Chrome 扩展目录。"
  read -r "?按回车关闭..."
  exit 1
fi

open "$EXTENSION_DIR"
open -a "Google Chrome" "chrome://extensions/" || true

echo ""
echo "DocFlow Chrome 扩展目录已经在 Finder 中打开。"
echo ""
echo "请在 Chrome 中完成一次性操作："
echo "1. 打开右上角“开发者模式”。"
echo "2. 点击“加载已解压的扩展程序”。"
echo "3. 选择 Finder 中刚打开的 docflow-chrome-extension 文件夹。"
echo "4. 刷新 DocFlow 页面，再点击“重新检测扩展”。"
echo ""
echo "这个步骤必须由你本人确认，脚本不会静默安装浏览器扩展。"
read -r "?完成后按回车关闭..."
