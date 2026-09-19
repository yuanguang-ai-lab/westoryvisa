# DocFlow 后端

`python3 -m backend.main 4176` 是独立后端 API 启动入口。

此目录拥有 API、SQLite、认证、客户档案、上传、OCR 调用、DS-160
映射、邮件、翻译、学校目录和现有 Agent 服务。

后端不会读取或提供 `frontend/` 中的任何文件。根目录 `server.py` 仅保留
旧同源运行兼容，不作为正式部署入口。

## 文档解析 Provider

文档上传和 API Token 都由后端处理，前端只调用 DocFlow `/api`：

- `OCR_PROVIDER=mineru`：使用 MinerU 精准解析 API，推荐 `MINERU_MODEL_VERSION=vlm`。
- `OCR_PROVIDER=docling`：继续使用本地 Docling Serve + RapidOCR。
- `OCR_PROVIDER=auto`：配置了 `MINERU_API_TOKEN` 时使用 MinerU，否则使用 Docling。

复制根目录 `.env.example` 为 `.env` 并填写 Token。`.env` 已被 Git 忽略。
MinerU 是异步接口，后端会完成签名上传、轮询、下载结果压缩包，并把
`full.md` 与 `content_list.json` 转换为现有 DS-160 映射层需要的格式。
