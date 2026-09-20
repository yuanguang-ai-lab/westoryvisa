# 四语言前端独立发布

只更新中文（默认）、Español、Português、English 的前端功能。准确生产基线是
`e553b7f7fed5ed09af0914e006e6660dce3e477e`，不是旧仓库根目录后端。
`PROVENANCE.json` 保持原样，`manifest.json` 单独记录 13 个替换、3 个新增及完整 48 个前端文件的最终哈希。
全部 366 个原始来源文件均核验；非白名单原文件必须不变。

生产静态目录原有的 AppleDouble `._*` 元数据不属于原始 45 个应用文件清单，但不会被删除或忽略：prepare 收集全部（含子目录）元数据文件哈希到私有收据，检查干净回滚镜像与线上完全一致，候选、切换后及回滚都必须保持相同元数据文件集和哈希。

## 边界

- 不覆盖宿主机 Compose、Nginx 配置，不构建/重启后端、调度器、OCR、Agent、数据库。
- 从当前已验证的生产前端镜像构建，只 COPY 16 个白名单静态文件。
- prepare 不切换线上：检查当前 45 个静态文件及实际 Nginx、检查干净原镜像与线上一致；保存原镜像独立标签和镜像归档、静态副本、Nginx 副本。
- 使用原 Compose 标签给出的**完整有序配置文件和原工作目录**，另加独立 image-only 覆盖文件。不会修改已有配置。
- 候选容器用 `compose run --no-deps`，只发布随机 loopback 端口，不能领取 `frontend` DNS 别名；校验 48 个文件、Nginx、完整运行配置指纹、网络、端口、API revision 22 和无登录浏览器查看拒绝访问，然后停止保留候选。
- activate 再次核对原容器 ID/镜像/文件/运行指纹、其他容器、有效 Compose 环境输入及 activeJobs=0，才 `up -d --no-deps --no-build --pull never frontend`。
- 切换后 HTTP/文件/运行配置核验失败自动尝试仅回滚前端。若发现其他人并发改动或配置漂移，拒绝擅自覆盖，明确报告人工处理。
- 不删除旧镜像、归档、已有目录、数据卷；不使用 compose down 或 prune。
- 不读取客户记录、上传文件、浏览器会话、支付信息，不执行登录或支付。Docker 环境变量仅在内存中比较；日志和收据仅留哈希，不保存值。

## 命令

需 Python 3 标准库、Docker 和支持 `run --interactive=false` 的 Compose v2。先从经核实的 GitHub **完整不可变提交 SHA** 下载归档并确认对应提交；不可使用浮动分支压缩包。脚本不依赖 `.git`，传入的 commit 是操作者核实的来源标识，文件内容另以 manifest 和原 PROVENANCE 校验。

从归档源目录运行，以下 `<FULL_COMMIT>` 和 `<COMMIT12>-<UNIQUE_SUFFIX>` 必须替换。状态目录须是 `/opt/westoryvisa-releases/` 下面新的单层目录，不得是源码目录或其父目录；prepare 会创建它，拒绝覆盖已有目录。

```sh
python3 releases/four-language-20260920/deploy.py verify-source --commit <FULL_COMMIT>
python3 releases/four-language-20260920/deploy.py prepare --commit <FULL_COMMIT> --state-dir /opt/westoryvisa-releases/<COMMIT12>-<UNIQUE_SUFFIX>
```

只有 prepare 返回 `prepared` 后，人工检查摘要并单独批准切换：

```sh
python3 releases/four-language-20260920/deploy.py activate --commit <FULL_COMMIT> --state-dir /opt/westoryvisa-releases/<COMMIT12>-<UNIQUE_SUFFIX>
```

显式回滚：

```sh
python3 releases/four-language-20260920/deploy.py rollback --commit <FULL_COMMIT> --state-dir /opt/westoryvisa-releases/<COMMIT12>-<UNIQUE_SUFFIX>
```

所有步骤打印简短 JSON。`receipt.json`、`rollback-image.tar`、原静态文件、原 Nginx 与新/旧镜像覆盖文件都保留在仅 root 可读的状态目录。prepare 失败不要复用同一状态目录；先检查摘要，使用新的唯一后缀重试。

**后续发布提醒：** 新镜像由独立 `new-image.yml` 覆盖文件选择，原宿主机配置未改。未来必须保留当前生效的完整 Compose 文件列表（可从运行容器标签确认）；直接只用旧 Compose 文件重建前端可能退回旧镜像。脚本的主机锁只协调采用本工具的发布，不能阻止其他管理员同时执行命令。

## 本地验证

```sh
python3 releases/four-language-20260920/build_manifest.py
python3 releases/four-language-20260920/tests_deploy.py
```

单元测试不访问 Docker 或网络。实际候选运行验证由服务器 prepare 完成；不要把本地单元测试当作已部署证明。
