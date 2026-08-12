# DS-160 条件分支规则

当前规则版本：`ds160-bfj-2026-07-17-v10`

## 定位

这是一套供签证中介、文案老师和签证顾问使用的工作规则，不是美国国务院公开的完整 CEAC 程序逻辑，也不提供法律判断。题目是否出现仍可能受签证类别、申请人资料、申请地点及 CEAC 当前版本影响，最终以当次页面为准。

## 当前覆盖

- B1/B2、F-1、J-1 分别生成适用的问题和材料槽位。
- 规则目录包含基础信息、旅行、同行人、赴美记录、联系方式、护照、美国联系人、家庭、工作教育、补充经历、健康、犯罪、国家安全、人权、移民记录、照片和协助填写。
- 父问题触发子问题；父答案改为不触发的选项后，相关子问题和附加数据会清空。
- 多段曾用名、同行人、赴美记录、前雇主、教育经历、服役记录等支持重复添加。
- 每题保存建议核对资料、来源、状态和顾问确认记录。
- 客户补充问卷中的中文自由文本会保存原文，并生成供 DS-160 使用的英文值；不确定转写会进入顾问核查。
- 单独填写 `D` 会标准化为 `DOES NOT APPLY`，但不会影响 Yes / No 分支答案。
- “是否已有具体旅行计划”始终保留；回答“否”时仍收集预计抵达日期和预计停留时长，回答“是”时才追加航班、离境日期和访问地点。
- 旅行计划与在美停留地址分开收集，避免把地址错误绑定在“是否已有具体旅行计划”的分支下。
- 当前为学生时，系统会复用已识别的学校资料；学习阶段为初中、高中或中学时，专业字段会隐藏且不计入缺失项。

## 自动解析边界

可以从材料辅助预填：

- 护照身份及证件字段
- I-20、DS-2019、DS-7002 中的 SEVIS、学校、项目和 Sponsor 字段
- 行程、航班、美国地址和联系人
- 当前工作或学校的客观资料
- 过往美国签证页上的号码、类别和签发日期

只允许客户或顾问回答：

- 拒签、拒绝入境、撤回入境申请和移民 petition
- 逾期、身份违规、递解、虚假陈述和协助非法入境
- 健康、药物、逮捕、定罪和其他犯罪问题
- 国家安全、人权、军事、准军事及特殊技能问题

这些问题没有默认 `No`，OCR 结果也不能写入其主答案。无论回答 `Yes` 还是 `No`，都需要顾问逐题确认。

## 数据位置

- `ds160_fields`：材料或条件问答形成的 DS-160 填写建议
- `field_evidence`：材料文件、页码、证据片段和置信度
- `ds160_answers`：主答案、触发字段、重复记录、来源和人工确认状态
- `review_issues`：缺失、冲突、低置信度和敏感题核查提醒

## 官方参考

- [美国国务院 DS-160 FAQ](https://travel.state.gov/content/travel/en/us-visas/visa-information-resources/forms/ds-160-online-nonimmigrant-visa-application/ds-160-faqs.html)
- [美国国务院学生签证说明](https://travel.state.gov/content/travel/en/us-visas/study/student-visa.html)
- [美国国务院交流访问签证说明](https://travel.state.gov/content/travel/en/us-visas/study/exchange.html)
- [美国国务院照片 FAQ](https://travel.state.gov/content/travel/en/us-visas/visa-information-resources/photos/frequently-asked-questions.html)
