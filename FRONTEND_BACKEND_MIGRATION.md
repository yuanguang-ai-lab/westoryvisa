# DocFlow 前后端分离完成记录

## 约束

- 每一步完成后，现有 `启动完整版本.command` 和 `启动数据库版.command` 必须可用。
- 不在本轮迁移中更换 OCR。
- 不在本轮迁移中开发或替换 Agent。
- 不改变现有 API 请求、响应和业务行为。
- 新实现通过测试和健康检查后，才移除旧兼容入口。

## 阶段 1：建立物理目录边界

状态：已实施。

- 浏览器端 HTML、CSS 和 JavaScript 迁入 `frontend/`。
- 后端从固定的 `frontend/` 目录提供静态资源。
- URL、同源 `/api`、Cookie 和启动脚本保持不变。

验收：

- `/`、`/product`、`/analytics` 可以正常访问。
- `/api/health` 正常。
- 后端单元测试与前端练习平台测试通过。

## 阶段 2：建立独立后端入口

状态：已实施。

- 新增 `backend` Python 包。
- `python3 -m backend.main` 成为独立 API 入口。
- 根目录 `server.py` 继续作为兼容入口。
- 后端实现和业务模块已全部迁入 `backend/`。

## 阶段 3：统一前端 API Client

状态：已实施。

- 新增 `frontend/api-client.js`。
- 工作台、产品页和统计页的网络请求统一经过 `DocFlowApi.request`。
- 请求 URL、参数、Cookie、错误处理和响应解析保持不变。

## 阶段 4：独立运行与跨域

状态：已实施。

- 前端通过 `runtime-config.js` 接收 API 地址。
- API Client 对跨域请求启用 Cookie credentials。
- 后端实现来源白名单、预检请求和可配置 Cookie。
- `frontend.dev_server` 与 `backend.main` 可以独立启动。

## 阶段 5：切换一键启动

状态：已实施。

- `scripts.run_local` 管理两个独立进程。
- 原有三个启动脚本均已切换到双进程模式。
- 页面地址仍从 4175 开始选择，用户使用方式不变。
- 根目录 `server.py` 保留旧同源回退模式。

## 阶段 6：生产部署边界

状态：已实施。

- 后端镜像只复制 `backend/`。
- 前端镜像只复制 `frontend/`。
- Nginx 是唯一公网入口，并将 `/api/` 代理给后端。
- SQLite 与上传文件使用独立持久化卷。
- 部署步骤和安全要求见 `DEPLOYMENT.md`。

## 最终边界

- 前端能够单独提供和部署。
- 后端能够单独启动和部署，不依赖前端文件。
- 本地兼容入口仍保持原项目运行方式。
- OCR 和 Agent 行为没有在本次架构迁移中替换。
