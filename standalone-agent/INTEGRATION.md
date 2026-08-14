# DocFlow integration

## 当前接入

本地兼容模式已经接入。`scripts.run_local` 同时启动 Agent Core、主后端和前端，
再通过 `DOCFLOW_AGENT_URL` 让主后端读取 Agent 的脱敏健康状态。Agent 不导入
DocFlow 后端、不读取主数据库路径，也不接收账号密码或验证码。

当前版本采用以下边界：

1. DocFlow 继续按现有方式上传、识别、展示证据和人工审核；
2. 顾问继续确认或修改字段；
3. Agent Core 以独立本地进程运行，主后端只展示连接状态和版本；
4. Computer Use 使用现有 Codex Desktop 短时任务交接；
5. Gemini/Playwright provider 在本地接入模式中被禁用；
6. 登录、CAPTCHA、敏感回答、签名、支付、最终提交仍由人工完成。

把字段任务进一步迁移到 Agent API、增加回调和替换剪贴板交接，仍是后续独立
决策，不属于当前健康状态与生命周期接入。

## Provider 适配

内置适配器位于 `visa_agent/adapters.py`，由 `build_service()` 自动注册：

- `document_parser`
- `ocr`
- `ocr_fallback`
- `extraction`
- `review`
- `translation`
- `computer_use`
- `browser`

服务由 `build_service(config, registry)` 组装。不要在 recognition、workflow、
safety、validation 或 orchestrator 模块中导入厂商 SDK。

内置注册名与 `.env.example` 一致：`mineru`、`paddle`、`deepseek`、`google`
和 `playwright`。部署端仍可在传入 registry 后覆盖同名注册。`ocr_fallback`
的 MinerU 主适配器可以实现 `parse()` 或
`recognize()`；组合器会在 MinerU 抛错、返回非文本或返回少于 3 个非空字符时
调用 PaddleOCR 兜底。Review 适配器除字段 `review()` 外，建议实现 `review_action()`，使
Computer Use 的二次复核与 Gemini 原生动作生成相互独立。

一个任务必须创建一个独立浏览器实例；不能跨客户复用 cookie、缓存或 profile。同一任务的打开、人工恢复、Gemini 启动、暂停和继续必须复用这一个实例，直到顾问显式取消。

浏览器生命周期分成两个端点：`open` 只打开官网供人工恢复既有申请，不调用模型；`start`/`resume` 只允许接管已经打开的同一实例。如果实例已经丢失，服务必须报错，不能静默创建替代浏览器。

## DocFlow → Agent 合同

后续任务适配层只发送：

- 短时、可撤销、限定任务 ID 的签名令牌；
- `startUrl`；
- 人工确认后的字段及确认审计信息；
- `requiredFieldIds`；
- 必要的证据引用，不发送主数据库路径；
- 租户和操作者的不可伪造身份声明。

Agent 回调只返回：

- 状态和状态版本；
- 动作审计事件；
- 人工接管原因；
- 已完成字段 ID；
- 错误代码；
- 不返回密码、验证码、完整 OCR 或浏览器凭据。

所有创建、审核、启动、恢复、取消和回调都要有幂等键。

## 页面计划

真实 CEAC 接入前，要依据受控测试逐页补全 `page_plans.py`：

- URL/标题匹配；
- `allowed_field_ids`；
- `required_field_ids`；
- `allowed_action_kinds`；
- 可点击按钮正则；
- 是否允许 Next；
- 是否为允许完成的终点页。

未知页面默认人工接管。不能让模型临时扩大权限。

## 上线门槛

- 真实 provider 的合同测试与故障注入测试通过；
- 页面计划与真实 CEAC 当前版本逐页验证；
- 加密检查点、租户密钥、访问控制和自动清理启用；
- 浏览器画面中的密码、验证码等敏感区域完成遮罩；
- 任务令牌短时、可撤销、不可跨任务使用；
- 审计日志具备完整性保护；
- 完成提示不等于最终提交；
- 保留随时人工接管和终止任务的入口。
