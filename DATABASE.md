# DocFlow DS-160 数据库说明

这个项目现在用的是 **SQLite 本地数据库**。SQLite 不是一个单独运行的数据库服务，它就是项目里的一个 `.sqlite3` 文件。

## 最简单启动方式

在 Finder 里打开项目文件夹，然后双击：

```txt
启动数据库版.command
```

终端窗口会打开，并显示：

```txt
网页地址：http://127.0.0.1:4175
数据库文件：.../data/docflow_ds160.sqlite3
```

然后浏览器打开：

```txt
http://127.0.0.1:4175
```

数据库连接状态和文件路径只在本地终端与这份开发说明中查看，不会显示在客户档案页面。

## 数据库在哪

数据库文件会在你第一次启动服务器并保存客户档案后生成：

```txt
data/docflow_ds160.sqlite3
```

完整路径一般是：

```txt
/Users/mac/Documents/Codex/2026-07-09/build-a-front-end-mvp-for/data/docflow_ds160.sqlite3
```

如果还没有 `data/` 文件夹，说明还没有成功启动数据库版，或者还没有保存任何客户档案。

## 用什么 App 打开数据库

推荐下载其中一个：

- **DB Browser for SQLite**：最简单，适合现在这个阶段
- **TablePlus**：更漂亮，也支持以后换 PostgreSQL
- **DBeaver**：功能很全，但稍重

当前阶段我建议用 **DB Browser for SQLite**。

打开方式：

1. 启动 DB Browser for SQLite
2. 选择 `Open Database`
3. 打开 `data/docflow_ds160.sqlite3`
4. 看这些表：
   - `organizations`
   - `users`
   - `clients`
   - `ds160_cases`
   - `documents`
   - `ds160_fields`
   - `field_evidence`
   - `ds160_answers`
   - `email_verifications`
   - `review_issues`
   - `audit_logs`
   - `auth_sessions`
   - `intake_links`

## 账号与档案隔离

- 新用户使用工作邮箱、密码、手机号、姓名和机构名称注册。
- 密码只保存 PBKDF2 哈希和随机盐值，不保存明文密码。
- 登录凭证保存在 `HttpOnly` Cookie 中，前端 JavaScript 无法读取登录令牌。
- 每个账号有独立 `user_key`，每份客户记录有独立 `record_key`。
- API 根据登录账号的 `organization_id` 强制筛选客户档案，不接受前端自行指定其他机构。
- 手机号当前作为辅助验证与找回信息保存；本地版不发送短信验证码。
- 当前开发阶段默认不校验验证码；邮箱和手机号仍作为账号信息保存。需要恢复邮箱验证时可通过 `REGISTRATION_VERIFICATION=email` 开启，配置见 `邮箱验证说明.md`。
- 本地 `data/` 目录权限为 `700`，数据库文件权限为 `600`，只允许当前 macOS 用户访问。

本地 SQLite 版本已经具备账号隔离和访问控制，但不等于生产级数据安全。正式上线时还需要 HTTPS、数据库磁盘加密、备份加密、密钥管理、短信服务和更完整的权限审计。

## 如果打不开网页

先确认终端里有没有这行：

```txt
DocFlow webpage: http://127.0.0.1:4175
```

如果没有，说明服务器没启动成功。

如果提示端口被占用，可以换端口：

```bash
python3 -m scripts.run_local 4188
```

然后打开：

```txt
http://127.0.0.1:4188
```

## API

- `GET /api/health`
- `GET /api/session`
- `POST /api/register`
- `POST /api/email-verification/send`
- `POST /api/login`
- `POST /api/logout`
- `GET /api/cases`
- `POST /api/cases`
- `PUT /api/cases/:id`
- `DELETE /api/cases/:id`
- `GET /api/ocr/health`
- `POST /api/cases/:id/documents/:documentId`
- `POST /api/cases/:id/scan`
- `GET /api/cases/:id/scan-status`
- `POST /api/cases/:id/intake-link`
- `GET /api/intake`（客户补充页通过请求头携带一次性令牌）
- `POST /api/intake`（客户提交缺失资料）

账号和客户档案必须通过 `backend/` 的 API 读取和保存。直接双击
`index.html` 不会启用账号登录，也不会把客户资料写入 `localStorage`。

文档扫描的安装、启动、数据位置和当前映射范围见 `文档扫描说明.md`。

`ds160_answers` 保存 DS-160 条件问题的主答案、触发字段、重复记录、人工确认状态和资料来源。安全背景问题不会写入默认答案。

`intake_links` 保存客户补充链接的令牌哈希、有效期和提交状态，不保存可直接打开的明文令牌。链接默认 30 天有效；顾问重新生成后，旧链接立即失效。明文链接只暂存在生成链接的浏览器本地，用于复制给客户。

`email_verifications` 保存注册验证码的 HMAC 哈希、有效期、失败次数和使用状态，不保存验证码明文。注册成功时间写入 `users.email_verified_at`。
