# DocFlow 前后端分离部署

## 当前部署边界

- `frontend/` 是纯静态浏览器应用，不导入任何后端代码。
- `backend/` 是纯 API、数据库和业务服务，不读取或提供前端文件。
- 本地一键启动由 `scripts/run_local.py` 同时管理两个独立进程。
- 生产环境由 Nginx 提供前端，并将 `/api/` 反向代理到后端容器。
- 根目录 `server.py` 只用于旧同源方式兼容，不是生产入口。

## 洛杉矶服务器首次部署

1. 安装 Docker Engine、Docker Compose 插件和受支持的 HTTPS 反向代理。
2. 将整个项目复制到服务器，进入 `deploy/`。
3. 复制 `backend.env.example` 为 `backend.env`，填写真实邮件和 MinerU 配置。Token 只能保存在后端。
4. 仅允许公网访问 80/443；不要公开 4176、5000、5001 或 11434。
5. 执行：

   ```bash
   docker compose up -d --build
   ```

6. 先访问 `http://服务器地址:8080` 验证，再把域名 HTTPS 代理到 8080。
7. HTTPS 启用后将 `DOCFLOW_COOKIE_SECURE` 设为 `true`。

## OCR 与数据

推荐设置 `OCR_PROVIDER=mineru`、`MINERU_MODEL_VERSION=vlm` 和
`MINERU_API_TOKEN`。后端负责签名上传、任务轮询和结果下载，前端不会接触
Token。若要保留本地降级，可继续通过 `host.docker.internal:5001` 连接
Docling/RapidOCR，并设置 `MINERU_FALLBACK_TO_DOCLING=true`；本地 OCR API
只能在内网监听并使用 `DOCLING_SERVE_API_KEY`。

SQLite、上传文件、扫描任务和邮件状态保存在 Docker 卷
`docflow_data` 中。正式使用前必须建立：

- 每日加密备份；
- 异机或对象存储副本；
- 定期恢复演练；
- 服务器磁盘加密和最小权限；
- 日志脱敏与访问审计；
- 护照和客户资料的留存、下载及删除策略。

## 分域部署

如前端与 API 使用不同域名：

1. 修改或覆盖 `frontend/runtime-config.js` 的 `apiBaseUrl`；
2. 将前端完整来源加入 `DOCFLOW_ALLOWED_ORIGINS`；
3. 根据域名关系配置 `DOCFLOW_COOKIE_SAMESITE`、`DOCFLOW_COOKIE_SECURE`
   和可选的 `DOCFLOW_COOKIE_DOMAIN`；
4. 必须通过 HTTPS 提供两端。

## 运维检查

```bash
docker compose ps
docker compose logs --tail=200 backend
curl -fsS http://127.0.0.1:8080/
curl -fsS http://127.0.0.1:8080/api/health
```
