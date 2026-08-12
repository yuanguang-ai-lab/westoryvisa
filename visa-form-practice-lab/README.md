# Visa Form Practice Lab

中文名：签证表格填写练习平台

这是一个独立制作、完全非官方的长表单填写练习网站。它用于课堂演示、流程熟悉、用户体验研究和前端工程展示，与美国政府、美国国务院、任何使领馆或签证服务机构没有隶属、合作、授权或背书关系。

本项目不能提交真实签证申请，不能付款、预约或连接政府系统。普通练习流程请使用虚构资料；DocFlow 专用导入页可在受信任的本机设备上接收当前客户档案的白名单字段，数据只保存在当前浏览器。

## 已实现功能

- 16 步本地练习流程，包括 Welcome、个人资料、联系信息、护照、旅行、同行人、历史、家庭、工作教育、背景练习、Review、打印和完成页。
- 配置驱动的字段、条件分支、日期关系、动态列表、错误摘要和字段定位。
- 空白练习与完整的 `Alex Example` 虚构示例。
- 800ms 防抖自动保存、手动保存、刷新恢复和当前步骤恢复。
- 草稿重命名、复制、删除、JSON 导入导出和一键清除。
- 中英文切换，不会因切换语言清空数据。
- 敏感字段遮盖、真实邮箱和非 DEMO 编号拦截、虚构电话与地址规则。
- 浏览器打印副本，包含 `PRACTICE COPY - NOT A VISA APPLICATION` 和水印。
- 无登录、无上传、无邮件短信、无追踪器、无广告、无远程字体、无远程 API。
- 桌面、平板和移动端响应式布局，以及键盘、焦点、ARIA 和减少动画支持。
- 可作为 DocFlow macOS Screen Agent 的独立本地目标站，将客户档案中的客观白名单字段保存成本机 Practice Lab 草稿。

## 与 DocFlow Screen Agent 联调

请从上一级 DocFlow 项目双击：

```text
启动Screen Agent演示.command
```

该命令会启动两个相互独立的本地站点：

- DocFlow 默认使用 `http://127.0.0.1:4175`
- Visa Form Practice Lab 默认使用 `http://127.0.0.1:4188`

DocFlow 只把白名单内的客观字段和签证情景交给桌面 Agent。Screen Agent 使用 Apple Vision OCR 在可见页面中定位，通过鼠标和键盘输入，并回读页面的字段进度确认写入成功。结果通过 Practice Lab 自己的 `localStorage` 保存在本机浏览器；安全背景、拒签、逾期、犯罪和移民历史不会进入任务。

导入目标页是 `screen-agent-import.html`。该页面只接受带本地任务编号和字段白名单的 URL，不包含提交按钮，并在 `Security and Background` 前要求人工接管。

## 为什么没有 React 和 Tailwind

当前开发环境无法访问 npm registry，也没有缓存 React、Vite、Tailwind 或 Zod。为保证项目现在即可运行，项目使用原生 ES Modules、TypeScript 源文件、HTML 和手写 CSS，且没有运行时依赖。

开发服务器与构建脚本使用 Node.js 24 内置的 TypeScript 类型移除能力。字段配置、验证、存储、视图和控制器仍然保持模块化，并非把全部代码塞进一个文件。未来网络可用时，可以逐模块迁移到 React，而不需要重做数据模型。

## 运行环境

- macOS、Windows 或 Linux
- Node.js 24 或更高版本
- 现代 Chromium、Safari 或 Firefox 浏览器

本项目不需要数据库，也不需要 `.env` 密钥。

## 启动网站

macOS 可以直接双击：

```text
启动练习平台.command
```

也可以在终端运行：

```bash
cd /Users/mac/Documents/Codex/2026-07-09/build-a-front-end-mvp-for/visa-form-practice-lab
npm run dev
```

然后打开：

```text
http://127.0.0.1:4188/
```

开发服务器不会接收或记录表单数据，只提供本地静态文件。

## 构建

```bash
npm run typecheck
npm run build
npm run preview
```

生产文件会生成在 `dist/`。项目使用 hash 路由，因此可以把 `dist/` 整体部署到普通静态托管服务，不需要设置 SPA 回退规则。

`npm run typecheck` 在当前零依赖环境中执行 TypeScript 语法和可转换性检查。它不等价于安装完整 `typescript` 包后的语义类型检查。

## 测试

单元和组件渲染测试：

```bash
npm test
```

覆盖内容包括：

- 字段配置唯一性和 16 步结构
- 日期关系和条件字段
- 条件隐藏后的幽灵数据清理
- 动态数组及时间顺序
- 完成度与练习编号
- 本地存储序列化
- 草稿复制、重命名和删除
- JSON 导入结构、大小和深度限制
- 疑似真实邮箱与 DEMO 标识规则
- 免责声明、确认框、错误摘要、动态条目和打印标识
- 中英文选择与回退

Playwright 完整流程：

```bash
npm run test:e2e
```

E2E 测试会自动构建、启动仅监听 `127.0.0.1` 的临时服务器，并使用本机 Chrome。Codex 桌面环境自带的 Playwright 路径会被自动识别；在其他电脑上，请先运行：

```bash
npm install --save-dev playwright
```

也可以用 `CODEX_PLAYWRIGHT_PATH` 和 `PLAYWRIGHT_CHROME_PATH` 指定本地安装位置。测试不会访问任何外部或政府接口。

## 本地数据在哪里

草稿保存在当前浏览器当前站点的 `localStorage`，键名为：

```text
vfpl_drafts_v1
```

语言和安全引导记录使用独立键。网站没有数据库，也不会把练习字段发送到服务器。

以下操作会导致数据丢失：

- 在草稿页删除单条草稿或一键清除全部数据
- 清理浏览器网站数据
- 使用某些浏览器的无痕窗口并关闭窗口
- 更换浏览器、设备、域名或端口

导出的 JSON 文件保存在用户自己的设备上，由用户自行保管。导出前会再次要求确认其中不含真实个人信息。

## 隐私与合规设计

- 每页顶部持续显示非官方声明。
- 首页和开始页展示完整免责声明。
- 未勾选虚构资料承诺时，不能创建练习。
- CSP 的 `connect-src` 设置为 `none`，阻止前端网络请求。
- 不包含文件上传、登录注册、邮件、短信、支付、预约或提交能力。
- 不使用官方徽标、印章、国旗、截图或 CEAC 视觉样式。
- 安全背景题经过概括和教学化重写，不逐字复制官方问题。
- 不提供通过率、风险分数、获批或拒签预测。
- 打印页没有条形码、确认码、政府编号、签名或印章。

## 代码结构

```text
visa-form-practice-lab/
├── index.html
├── styles.css
├── src/
│   ├── app.ts          # 路由、事件、自动保存和弹窗控制
│   ├── config.ts       # 16 步配置与字段 schema
│   ├── example.ts      # 完整虚构示例
│   ├── form.ts         # 字段与动态列表渲染
│   ├── i18n.ts         # 中英文 UI 字典与语言选择
│   ├── storage.ts      # localStorage、导入和导出
│   ├── validation.ts   # 条件、验证和完成度
│   ├── views.ts        # 页面视图
│   ├── ui.ts           # 全局布局与步骤导航
│   ├── icons.ts        # 本地 SVG 图标库
│   └── types.ts        # 统一 TypeScript 类型
├── scripts/
│   ├── dev-server.mjs
│   ├── build.mjs
│   ├── typecheck.mjs
│   └── run-playwright.mjs
├── tests/
├── e2e/
└── .env.example
```

## 增加章节或字段

1. 在 `src/config.ts` 的 `STEPS` 中增加步骤或字段。
2. 为新字段提供稳定、唯一的 `id`，并同时提供英文和中文标签。
3. 需要条件显示时使用 `condition`，不要在视图中写零散判断。
4. 需要虚构数据限制时使用 `fictionalRule`。
5. 在 `src/example.ts` 中补充对应的明显虚构示例。
6. 在 `src/validation.ts` 中增加跨字段规则。
7. 为新逻辑补充 `tests/` 和 `e2e/` 测试。

## 维护翻译

通用导航、按钮、状态和错误文案位于 `src/i18n.ts`。字段标题和章节说明以 `{ en, zh }` 结构保存在 `src/config.ts`。新增内容必须先提供英文，再提供中文；缺少指定语言时会回退到英文或安全短标签。

## 更换品牌名称

需要同步检查：

- `index.html` 的页面标题
- `src/i18n.ts` 的 `app.name` 与 `app.nameEn`
- `src/storage.ts` 的 `APP_NAME`
- `src/views.ts` 的打印页脚
- `README.md` 和 `.env.example`

不要把品牌改成会让用户误认为政府官网的名称，也不要加入 `Official`、政府机关名称或官方徽标。

## 已知限制

- 这是教学化、简化和重新组织的长表单，不代表当前真实 DS-160 的完整字段、原文、顺序或法律要求。
- `localStorage` 不适合真实敏感数据，也不能跨浏览器或设备同步。
- 没有服务器账户、云备份或多人协作。
- 浏览器对打印分页的实现不同，打印前应查看系统预览。
- 原生日期控件的外观由操作系统与浏览器决定。
- 当前零依赖类型检查是语法级检查；完整语义类型检查需要未来安装 TypeScript 工具链。

## 非官方声明

Unofficial educational simulation. Not affiliated with or endorsed by the U.S. Government.

本项目不能替代官方说明、法律建议或具备资质的专业意见。真实申请应由用户自行确认当前政府官网域名、最新要求和提交步骤，且不得把本练习数据自动传入外部网站。
