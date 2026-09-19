# WestoryVisa 前端

此目录包含当前可运行的 DocFlow 浏览器端资源。

- `product.html`：公开产品与宣传页面。
- `workspace.html`、`app.js`、`styles.css`：机构工作台与客户补充入口。
- `membership.html`、`billing.js`：会员、价格、购买和账户页面。
- `extension.html`：Chrome 插件安装说明。
- `runtime-config.js`：部署时指定后端 API 地址。
- `api-client.js`：浏览器端统一请求入口。
- `product.*`：产品介绍页。
- `analytics.*`：访问统计页。
- `screen-agent-target.*`：历史演示目标页。

前端是独立静态应用，不导入后端 Python 代码。默认通过同源 `/api` 调用后端；
独立开发服务器会动态写入后端地址：

```bash
python3 -m frontend.dev_server 4175 http://127.0.0.1:4176/api
```

生产环境可以直接由 Nginx/CDN 提供此目录，并通过 `runtime-config.js`
配置独立 API 域名。

## 语言系统

公开站点与工作台提供 `中文`、`Español`、`Português`、`English` 四种显示语言，首次访问默认中文。
选择结果写入 `?lang=zh-CN`、`?lang=es`、`?lang=pt-BR` 或 `?lang=en` 并保存在浏览器中。
明确选择的语言优先于默认值。显示语言与机构/客户的业务国家相互独立。

- `site-language.js`：语言选择、URL 传播、动态内容翻译。
- `site-translations.js`：公开页面、工作台、法律和插件说明的补充译文。
- `intake-i18n.js`：客户补充表单的字段、问题和校验文案。

上线前可在仓库根目录运行：

```bash
npm install --prefix tests/frontend --package-lock=false
npm test --prefix tests/frontend
```

详细范围与兼容性检查见 [`tests/frontend/README.md`](../tests/frontend/README.md)。
这些检查使用模拟后端与虚构资料，不连接生产数据；生产前端仍要求 API revision 22 或更新版本。
