# DocFlow Standalone Agent

这是 DocFlow 的单智能体内核。通过项目根目录的本地启动器运行时，它会作为
独立进程随 DocFlow 一起启动，主后端只读取脱敏健康状态；Agent 仍不导入主后端、
不读取主数据库。当前独立表单执行器使用 Gemini 原生 Computer Use 视觉闭环，
保留人工确认、逐步校验和安全边界，不依赖 Codex 运行。

## 设计结果

一个 `AgentOrchestrator` 负责完整任务，但不同能力由可替换的 provider 提供：

```text
AgentOrchestrator
├── DocumentParser       MinerU
├── OCRProvider          MinerU → PaddleOCR 兜底
├── ExtractionModel      DeepSeek-V4-Flash
├── ReviewModel          DeepSeek-V4-Flash 独立复核
├── TranslationProvider  DeepSeek-V4-Flash 翻译/拉丁转写
├── ValidationEngine     Python 规则，不能被模型替换
├── HumanReview          人工确认及审计来源
├── ComputerUseModel     Gemini 3.6 Flash 原生 Computer Use
└── Browser Runtime      Playwright（截图、鼠标、键盘与确定性校验）
```

更换某个模型只需要更换对应环境配置并注册适配器，不需要改状态机、安全策略或
其他模型。`visa_agent.providers.ProviderRegistry` 是唯一的 provider 组装入口，
`visa_agent.factory.build_service` 负责按配置构建服务。

## 已实现能力

- ICAO TD3 护照 MRZ 规则解析和校验位验证；
- 模型字段严格白名单、类型/格式、置信度和原文证据回指校验；
- 风险等级由系统赋值，模型不能降低风险或自行确认字段；
- 人工确认记录确认人、时间、来源、原值、确认值和原因；
- 跨文档字段合并、冲突提示和人工复核状态；
- CEAC 页面计划：每页字段、动作、按钮、必填项均有代码白名单；
- 导航执行前验证 HTTPS、域名、路径、凭据和敏感查询参数；
- 模型只选择字段，实际写入值始终来自人工确认记录；
- 动作结果通过 DOM/控件值等确定性状态验证，模型验证只作二次复核；
- 模型发出 `COMPLETE` 后仍由系统检查必填字段、页面错误和待处理动作；
- 生产默认使用 `visual` 模式：Gemini 每一步查看当前截图，返回一个原生鼠标或
  键盘动作；Playwright 执行后重新截图，并通过 `function_result` 和
  `previous_interaction_id` 延续同一条视觉推理链；
- DOM 不负责决定或代替表单操作，仅在动作后读取控件值做确定性校验；普通误点或
  写入失败会把验证结果和新截图交回 Gemini，最多自动纠正三次；
- 模型不接收或决定字段值；实际值始终由系统从人工确认记录注入；
- 动作 ID、执行前检查点和恢复后状态核对，避免重复写入；
- 登录、验证码、敏感问题、签名、支付和最终提交强制人工处理；
- 可恢复的类型化任务检查点、证据最小化及可选 Fernet 加密；
- 独立 HTTP API；不导入 DocFlow，也不读取 DocFlow 数据库。

任务状态：

```text
created → parsing_documents → extracting_fields → validating
        → waiting_review → ready_for_form → filling_form
        → waiting_human / review_required
        → completed / blocked / failed / cancelled
```

## 模型与 provider 配置

复制 `.env.example` 后，可以分别设置：

- `DOCUMENT_PARSER_*`
- `OCR_*`
- `OCR_FALLBACK_*`
- `EXTRACTION_*`
- `REVIEW_*`
- `TRANSLATION_*`
- `COMPUTER_USE_*`
- `BROWSER_*`

`.env.example` 已按当前选型填写 provider 与模型名，但不包含任何密钥。内置
适配器已实现并由 `build_service()` 自动注册。服务启动时会读取
`standalone-agent/.env`；系统环境变量优先于文件值。

当前路由为：

1. PDF、DOC、DOCX 等版面文档直接使用 MinerU `vlm`；
2. 图片和扫描件优先使用 MinerU `vlm`，调用失败或返回空/极短文本时用
   PaddleOCR `PP-OCRv6`；
3. 结构化字段抽取使用 `deepseek-v4-flash`，输出仍经过白名单、格式和证据校验；
4. 翻译、拉丁转写与独立复核使用 `deepseek-v4-flash`；
5. Computer Use 使用 Gemini `gemini-3.6-flash` 原生 Interactions API +
   Playwright 可视浏览器，运行时不需要 Codex；
6. `AGENT_COMPUTER_USE_EXECUTION=visual` 强制逐步截图—动作—结果—再截图闭环；
   `hybrid` 只保留作本地测试或低风险页面的快速模式；
7. `AGENT_BROWSER_STARTUP_TIMEOUT_SECONDS` 默认 30 秒（部署可设 5–60 秒）；
   启动超时会精确关闭该任务私有 profile 的 Chrome，并由持久检查点自动重建；
8. 人工确认、Validation Engine、检查点与浏览器隔离不由模型替代。

旧的 `MODEL_*` 变量仍可作为 extraction 和 computer-use 的兼容回退，但新部署
应使用分方向变量，避免两个任务被绑到同一个模型。

## 本地运行

先安装生产适配器依赖和 Chromium：

```bash
cd standalone-agent
cp .env.example .env
python3 -m pip install -e '.[all]'
playwright install chromium
python3 -m visa_agent config
```

填入 `.env` 中的 `DEEPSEEK_API_KEY`、`GEMINI_API_KEY`、
`MINERU_API_TOKEN` 和 `PADDLEOCR_ACCESS_TOKEN`。默认配置全部使用官方云 API：
MinerU Precision Extract、PaddleOCR Official API、DeepSeek API 和 Gemini API。
Playwright 只负责在本机隔离执行浏览器动作，它不是模型。然后运行：

```bash
python3 -m visa_agent recognize examples/sample_passport_mrz.txt
python3 -m visa_agent demo --data-dir /tmp/docflow-agent-demo
python3 -m visa_agent serve --port 8765
```

若只安装检查点加密依赖：

```bash
python3 -m pip install -e '.[secure]'
```

然后配置 `AGENT_CHECKPOINT_ENCRYPTION_KEY`。环境加载的服务默认拒绝明文检查点；
只有离线 demo 和显式开发配置允许明文。

## 独立 API

- `GET /health`
- `POST /v1/recognize-text`
- `POST /v1/recognize-document`（Base64 文件，最大 25 MB）
- `POST /v1/transform-text`
- `POST /v1/jobs`
- `GET /v1/jobs/{jobId}`
- `POST /v1/jobs/{jobId}/review`
- `POST /v1/jobs/{jobId}/open`（只打开官网，不调用 Gemini）
- `POST /v1/jobs/{jobId}/start`
- `POST /v1/jobs/{jobId}/resume`
- `POST /v1/jobs/{jobId}/cancel`

原始文件识别请求：

```json
{
  "filename": "passport.pdf",
  "mediaType": "application/pdf",
  "documentType": "passport",
  "fileBase64": "<base64>"
}
```

独立 provider 模式下，`open` 为当前任务创建一次隔离的 Playwright 浏览器，
由人工恢复既有 DS-160 申请；`start` 和 `resume` 只复用该浏览器并调用 Gemini，
绝不会自行创建替代会话。未配置密钥、未安装
Chromium、页面不在 CEAC 白名单或页面计划不匹配时会明确停止，不会绕过安全门。

创建任务时所有传入字段都会被重置为未确认，之后必须调用 `review`：

```json
{
  "startUrl": "https://ceac.state.gov/GenNIV/...",
  "requiredFieldIds": ["personal.surname"],
  "fields": [
    {
      "id": "personal.surname",
      "value": "ZHANG",
      "confidence": 0.94
    }
  ]
}
```

## 当前边界

本地启动器现在负责 Agent 进程生命周期和健康状态接入，但 Agent 仍不读取
DocFlow 数据库。它不会自动登录、处理 CAPTCHA、回答安全/背景问题或执行最终
提交；这些步骤继续强制人工接管。具体见 `INTEGRATION.md` 和 `SECURITY.md`。
