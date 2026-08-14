# DocFlow Computer Use V2

`local_supervisor.py` is the V2 launcher's original local service supervisor,
kept next to `run_docflow.py` so the V2 source remains self-contained and does
not collide with unrelated project-level `scripts/run_local.py` modules.

这是与现有 `standalone-agent` 并存的第二套执行层。V1 默认行为保持不变；
V2 的所有输入也严格限制在自己控制的浏览器页面内。

V2 只替换 Computer Use 的运行策略，继续复用旧版已经验证过的：

- 字段、任务和加密检查点格式；
- DS-160 页面白名单与安全策略；
- Playwright 控件定位、值写入和精确校验；
- Gemini Interactions API 协议；
- AgentService 的浏览器隔离和任务恢复。

## V2 的关键变化

1. **语义优先，Gemini 兜底**
   已知 DS-160 页面先用代码拥有的字段标签和控件提示定位。只有无法唯一定位
   的字段才发送给 Gemini。旧版 `visual` 模式强制每个页面先调用 Gemini；
   V2 不再这样做。

2. **规划策略与可视操作分离**
   Agent 使用 `hybrid` 规划策略，但浏览器仍使用 `visual` 操作风格。顾问仍能
   看见鼠标移动、输入和状态提示，Gemini 不再成为正常字段的速度瓶颈。

3. **所有重试都有上限**
   Next 派发后最多进行 3 轮只读观察；普通无进展最多 5 轮；Gemini 连续服务
   故障最多 3 轮。超过预算后进入明确的人工硬边界，不会永久轮询或重复点击。

4. **动态刷新采用真实异步状态**
   V2 同时监听 ASP.NET `PageRequestManager` 和浏览器真实的
   document/XHR/fetch 请求。请求已经发出时会等响应和 DOM 更新；仅调用
   `__doPostBack`、但没有实际请求或 DOM 替换时，才在短宽限期后继续，
   不再固定空等 8 秒，也不会抢跑慢 postback。

5. **Gemini 预算缩短**
   Gemini 只处理少量未解析字段，单次主请求最多 30 秒，短恢复请求最多
   4 秒。字段值仍由系统注入，模型只做视觉定位。

6. **分支必填资料启动前预检**
   旅行计划和付款方等会在 postback 后显示新必填控件。V2 在创建或同步
   任务时检查这些分支来源；日期、停留时长、付款机构资料等不完整时，
   直接返回中文缺项列表，不会填到 CEAC 中途才停住。

7. **全部输入限制在网页控件内**
   V2 先用 DOM 语义唯一定位并核验目标，再通过该 Playwright locator 完成
   普通下拉、日期、停留时长及联动框的改值。运行时不会申请 macOS 辅助功能、
   激活其他窗口、扫描桌面高亮区域或发送全局鼠标键盘事件。

## 本地运行

V2 依赖旧版的稳定领域模型，但不修改旧版源码：

```bash
cd standalone-agent-v2
PYTHONPATH=../standalone-agent:. ../standalone-agent/.venv/bin/python \
  -m unittest discover -s tests -v
```

独立启动 V2（无需修改旧版启动文件）：

```bash
cd standalone-agent-v2
PYTHONPATH=../standalone-agent:. ../standalone-agent/.venv/bin/python \
  -m visa_agent_v2 serve --port 8766
```

前端灰度到 V2 时只需将 Agent Core 地址指向 `http://127.0.0.1:8766`。
正式替换后可让 V2 使用原端口；旧服务仍可随时重新启动回退。
V2 默认把检查点写入旧配置目录名加 `-v2` 的独立目录；也可用
`AGENT_V2_DATA_DIR` 显式指定。不要让 V1/V2 同时写同一个检查点目录。

日常使用无需分别启动三个服务。先停止手工运行的 V2，再双击项目根目录的
`启动V2完整版本.command`。该启动器会把 V2、DocFlow 后端和前端接到同一
套端口与健康监控中，并自动打开网页。

如果以后允许在现有 Python 入口切换，也可以将原来的：

```python
from visa_agent.factory import build_service
```

替换为：

```python
from visa_agent_v2.factory import build_fast_service as build_service
```

其余 HTTP API、任务格式和前端调用保持不变。V2 浏览器 profile 位于自己的
`browser-profiles-v2/`，不会与旧版同时占用同一个 profile。
V2 不需要 macOS“辅助功能”权限；健康接口中的 `selectInputBackend` 应为
`playwright-scoped`，且 `globalInputDisabled` 必须为 `true`。

## 发布门槛

V2 进入真实业务前必须同时满足：

- 映射字段的 Gemini 调用率不高于 5%；
- 普通字段执行/校验 p95 不高于 1.5 秒；
- 动态字段执行/稳定 p95 不高于 4 秒；
- 单页 Gemini 兜底最多 1 次；
- Next 页面不变时 35 秒内停止自动观察并给出明确原因；
- 50 次完整合成 DS-160 流程全部到达 Review，必填字段零漏填；
- Next、Add Another 等非幂等点击零重复；
- Gemini 超时、浏览器重连、DOM 替换和慢 postback 注入测试全部通过。

外部网站宕机、验证码、会话过期或未提供必填资料无法由执行层保证；这些情况
必须在上述时间边界内明确交还人工，不能表现为卡住。
