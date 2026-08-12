# CEAC 页面路径覆盖说明

## 官方资料能确认什么

美国国务院公开说明了 DS-160 的必填规则、页面错误提示、`Next` 导航和最终复核流程，但没有公开一份保证长期稳定的 CEAC 内部 ASP.NET URL / `node` 全量清单。

- DS-160 FAQ: https://travel.state.gov/content/travel/en/us-visas/visa-information-resources/forms/ds-160-online-nonimmigrant-visa-application/ds-160-faqs.html
- DS-160 overview: https://travel.state.gov/content/travel/en/us-visas/visa-information-resources/forms/ds-160-online-nonimmigrant-visa-application.html
- CEAC start: https://ceac.state.gov/GenNIV/Default.aspx

因此，页面路径不能被当成官方稳定 API。DocFlow 使用“已知语义映射 + 运行时路径捕获”的方式维护覆盖率。

## 当前实际观察到的路径

以下路径来自本机 Chrome 中真实打开过的 CEAC 页面，不代表美国国务院对内部 URL 的兼容承诺：

| 模块 | node | path |
|---|---|---|
| Personal Information 1 | `Personal1` | `/GenNIV/General/complete/complete_personal.aspx` |
| Personal Information 2 | `Personal2` | `/GenNIV/General/complete/complete_personalcont.aspx` |
| Travel Information | `Travel` | `/GenNIV/General/complete/complete_travel.aspx` |
| Travel Companions | `TravelCompanions` | `/GenNIV/General/complete/complete_travelcompanions.aspx` |
| Previous U.S. Travel | `PreviousUSTravel` | `/GenNIV/General/complete/complete_previousustravel.aspx` |
| Address and Phone | `AddressPhone` | `/GenNIV/General/complete/complete_contact.aspx` |

## 运行时捕获

Chrome 扩展会在每次 CEAC 页面加载后记录：

- 页面 path
- `node` 参数
- 页面标题
- 是否匹配当前任务中的页面计划

不会记录完整查询字符串、验证码、客户字段值、登录凭据或 CEAC 会话标识。记录只保留最近 30 个唯一路径。

已映射页面会按白名单字段填写。未映射页面会暂停并在 DocFlow 显示捕获到的 `node`，不会盲点 `Next`。

## 导航模式

- 默认：Agent 填完并检查当前页，顾问在 CEAC 点击 `Next`，下一页加载后自动继续。
- 可选：开启“连续自动跳转”后，普通已映射页面只有在全部计划字段写入成功、可见必填项完整且无页面错误时才点击 `Next`。
- 固定停止：验证码、登录凭据、安全与背景判断、电子签名、法律声明、付款和最终提交。
