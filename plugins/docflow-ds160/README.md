# DocFlow DS-160 Computer Use 插件

这个本地插件让 Codex 读取 DocFlow 生成的短时字段任务，并通过官方 Computer Use 能力操作用户可见的 Chrome 页面。它不需要 DocFlow Chrome 扩展。

## 本地安装

```bash
codex plugin marketplace add /path/to/build-a-front-end-mvp-for
codex plugin add docflow-ds160@docflow-local
```

安装或更新后新建一个 Codex 任务，使新版 Skill 生效。完整使用流程和边界见项目根目录的 `SCREEN_AGENT.md`。

## 约束

- 任务地址只允许指向本机 `127.0.0.1`。
- 只使用 Computer Use 的可见系统操作。
- 不使用 Chrome 扩展、DOM 注入、Playwright、Selenium 或第三方 RPA。
- 不处理验证码、凭据、敏感判断、签名、付款、预约确认或最终提交。
