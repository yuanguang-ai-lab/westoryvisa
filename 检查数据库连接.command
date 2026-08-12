#!/bin/zsh
cd "$(dirname "$0")"
echo "正在检查 DocFlow DS-160 数据库连接..."
echo ""

FOUND=""
for PORT in {4175..4194}; do
  if curl -s "http://127.0.0.1:${PORT}/api/health" | grep -q '"ok": true'; then
    FOUND="$PORT"
    break
  fi
done

if [ -n "$FOUND" ]; then
  echo "✅ 服务器正在运行"
  echo "网页地址：http://127.0.0.1:${FOUND}"
  echo "API 状态："
  curl -s "http://127.0.0.1:${FOUND}/api/health"
  echo ""
  echo ""
  open "http://127.0.0.1:${FOUND}"
else
  echo "❌ 没有检测到正在运行的本地服务器。"
  echo ""
  echo "请先双击：启动数据库版.command"
  echo "或者在终端运行："
  echo "python3 -m scripts.run_local 4175"
fi

echo ""
if [ -f "data/docflow_ds160.sqlite3" ]; then
  echo "✅ 数据库文件存在：$(pwd)/data/docflow_ds160.sqlite3"
else
  echo "⚠️ 数据库文件还没有生成。启动数据库版并保存客户档案后会出现。"
fi

echo ""
echo "按回车关闭窗口。"
read
