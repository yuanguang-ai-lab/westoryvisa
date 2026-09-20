# 四语言前端回归检查

在仓库根目录运行（使用 Node.js 24，或满足 `package.json` 中要求的 Node.js 版本）：

```bash
npm install --prefix tests/frontend --package-lock=false
npm test --prefix tests/frontend
npm run test:compat --prefix tests/frontend
```

如果已在其他目录安装 `jsdom@29.1.1`，也可以设置 `NODE_PATH` 为该目录的 `node_modules` 路径，再直接运行：

```bash
node tests/frontend/audit-language.cjs
node tests/frontend/audit-runtime.cjs
node tests/frontend/audit-runtime.cjs --compat-only
node tests/frontend/audit-regressions.cjs
```

- `audit-language.cjs`：8 个页面 × 4 种语言，共 32 项；另检查 5 种默认/保存/URL 优先级场景，以及切回中文和保留用户输入的 1 项往返检查。
- `audit-runtime.cjs`：52 项动态渲染检查，包括产品页、登录页、客户补充页和 10 种工作台视图；另执行 4 项 API 版本兼容检查。
- `--compat-only`：仅运行 4 项兼容检查。生产发布版要求 API revision 22 或更新版本；revision 20 在生产域名和本机地址均必须被拒绝，即使存在旧的本地预览标记。
- `audit-regressions.cjs`：48 项语义回归检查。覆盖 4 种显示语言 × 4 个机构服务国家的登录/注册请求体和请求头；语言切换不得改变服务国家；国家选择保留已输入的表单数据；MX 档案按当前语言显示提示；普通账号国家锁和案例过滤、平台管理员国家切换入口、插件仅支持中国版的既有范围均保持。

测试读取 `components/frontend/www/`。全套共 **142 项**（38 项静态/优先级/往返、52 项动态界面、4 项 API 版本保护、48 项业务语义回归）。

每项检查输出一行 JSON；任一失败时退出码为 1。运行时网络响应与用户、机构、案例均为测试夹具，不访问真实服务器或客户资料。这些检查验证语言、链接传播、界面渲染和版本保护，不证明真实后端认证、支付、OCR、AI 或案例保存正常，也不替代人工审校译文和图片内文字。
