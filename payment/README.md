# Westory Visa 真实支付接入

## 当前状态

订单、支付流水、退款、会员期限、金额/币种核对、事件幂等、查单和会员门禁已经实现。真实通道在获得正式商户合同、接口文档和签名材料之前保持关闭。

候选通道并行申请：

1. 支付宝香港/跨境线上直接商户；
2. 微信支付香港及中国内地钱包跨境线上商户；
3. 香港本地收单机构统一 API（支付宝、微信支付、FPS，可选银行卡）。

不接入无实名商户合同、资金经过私人账户、无法说明法律收单方或结算方的“四方”通道。

## 已实现的业务边界

- 商品：月度会员 CNY 199 / 30 天、年度会员 CNY 1,990 / 365 天（币种和价格仍待管理层最终确认）。
- 当前方案是一次性购买固定期限，不自动续费。
- 订单、支付、退款和会员均绑定机构账户。
- 回调必须核对本地订单金额和币种，并按支付事件 ID 幂等处理。
- 支付成功只能由已验签的异步通知或可信查单结果确认，前端跳转不能开通会员。
- 购买前必须接受当前版本的服务条款、隐私政策和退款政策；订单保存条款版本和服务器接受时间。
- 公司法定名、BRN、地址和客服邮箱未配置完整时，后端禁止创建真实订单。
- 未购买或会员已过期的账户无法访问工作台和案件接口。

## 生产配置

公共商户资料放在后端环境变量中，由 `/api/merchant-profile` 安全公开给网站法律页面：

```env
MERCHANT_LEGAL_NAME_EN=
MERCHANT_LEGAL_NAME_ZH=
MERCHANT_BUSINESS_REGISTRATION_NUMBER=
MERCHANT_REGISTERED_ADDRESS=
MERCHANT_SUPPORT_EMAIL=
MERCHANT_SUPPORT_PHONE=
MERCHANT_SUPPORT_HOURS=
MERCHANT_REFUND_WINDOW_DAYS=7
BILLING_PUBLIC_BASE_URL=https://westoryvisa.com
```

通道选项：

```env
# pending_selection | alipay_cross_border | wechat_pay_cross_border | hk_acquirer | stripe
PAYMENT_PROVIDER=pending_selection
```

除现有 Stripe 测试实现外，其他通道不得使用推测的变量名、端点或签名算法。收到正式文档后按照 `PROVIDER_INTEGRATION_CONTRACT.md` 实现适配器。

## 上线前必须取得

1. 合同中的法律收单方、结算方和 Westory Visa 实名商户号。
2. 书面确认 SaaS 类目准入，以及中国内地钱包可向香港线上商户付款。
3. MDR、固定费、换汇、退款、月费、最低收费、储备金和结算周期。
4. 沙箱及生产端点、商户号、产品/通道编码和 IP/域名白名单要求。
5. 下单、查单、关单、退款、查退款、异步通知和对账文档。
6. 签名算法、字符编码、字段排序、密钥/证书、轮换与重放保护规则。
7. 完整状态码、通知重试和成功响应格式。

## 验收顺序

1. 沙箱成功支付与失败支付；
2. 错金额、错币种和伪造签名拦截；
3. 重复和乱序通知幂等；
4. 支付处理中及主动查单；
5. 全额/部分退款和异步退款结果；
6. 对账文件与本地订单逐笔核对；
7. 正式环境小额付款、退款和银行入账核对；
8. 密钥轮换、告警和运营后台验证。

KYC 文件、合同、银行资料、API 私钥和生产证书不得提交到 GitHub。
