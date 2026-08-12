#!/bin/zsh
set -e
umask 077

cd "$(dirname "$0")"
python3 configure_email.py

echo ""
echo "按任意键关闭窗口。"
read -k 1
