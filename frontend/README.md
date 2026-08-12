# DocFlow 前端

此目录包含当前可运行的 DocFlow 浏览器端资源。

- `index.html`、`app.js`、`styles.css`：机构工作台。
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
