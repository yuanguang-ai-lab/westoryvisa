# WestoryVisa：已核对的生产源码基线

这是 **2026-09-20（北京时间）从运行中容器采集并校验的源码基线**。
它不再把旧 `main` 的根目录代码混充为线上版本。业务代码没有在本次整理中修复或改写。

## 去哪里看、以后改哪里

| 组件 | 已归类目录 | 来源与边界 |
|---|---|---|
| 网站前端 | `components/frontend/www/` | 实际静态目录；首页是 `product.html`，不是 Nginx 默认 `index.html` |
| 生效的前端代理配置 | `components/frontend/nginx/default.conf` | 从运行中的前端容器读取；不能被旧宿主机配置覆盖 |
| API 后端 | `components/backend/app/` | 当前 API 容器中的代码 |
| 自动化调度 | `components/scheduler/app/` | 与 API 后端存在差异，单独保留 |
| OCR Worker | `components/ocr-worker/app/` | 更早的独立生产版本，单独保留 |
| 普通 Agent | `components/agent-standard/` | 两个运行实例的文件完全一致，只保留一份源码 |
| Chrome Agent | `components/agent-chrome/` | 与普通 Agent 不同，不混合覆盖 |
| 浏览器插件 1.0.5 | `components/browser-extension/` | 从实际分发 ZIP 解包；原 ZIP 仍保留在前端 downloads 中 |

`build-evidence/` 是采集的宿主机构建文件和源码候选，不是已经验证可一键部署的新构建系统。
`observed-dependencies/` 记录采集时实际安装的 Python 包版本，不是保证可复现的依赖锁。

## 这份归档证明什么

- 7 个应用容器的已核验源码路径，以及明确选择的构建文件；主采集包有 686 条文件记录。
- 351 个从主包保留的文件，加插件解包 10 个文件、补取 Chrome 构建 5 个文件，共 **366 个归类源码/资源/构建文件**。
- 每个归类文件的来源、字节数和 SHA-256 记录在 `PROVENANCE.json`。
- API、调度器、OCR 三份后端不相同；两个普通 Agent 相同；Chrome Agent 不同。
- 连续两次采集共同覆盖的 676 个文件哈希全部一致；采集前后容器身份未变化。

完整原始包、内部部署覆盖配置、运行清单和详细安全审核报告只保存在任务的本地私有交付中。
没有把环境变量值、密钥、数据库、客户文件、上传内容、浏览器会话或日志加入此分支。

## 分支用途

- `main`：旧仓库历史，未被此次整理修改；不能直接覆盖生产。
- `archive/production-public-20260920`：此前仅有公开 HTTP 文件的旧快照，**不完整**。
- `archive/production-server-20260920`：本次生产源码归档；以对应提交和 `production/source-snapshot-2026-09-20` 标签为固定参照，不在上面开发。
- `work/reconcile-production-20260920`：从本次基线继续整改的工作分支；以后每项改动再开自己的 `fix/...` 或 `feature/...` 分支。

具体规则见 [后续改动与发布约定](WORKFLOW.md)；边界见 [采集范围与缺口](SCOPE.md)。

## 本地核验

在此基线提交检出后运行：

```sh
python3 tools/verify_snapshot.py
```

这只核对原始文件清单及哈希，不启动应用、不连接服务器。
后续功能分支改动源码后，基线核验出现差异是预期结果；不要伪造原始采集记录让检查通过。

**这是一份源码来源基线，不是可立即覆盖生产的发行版，也不是数据库或完整机器备份。**
没有执行线上重建、重启、迁移或业务修复；详细风险需在后续分支、隔离测试环境中处理。
