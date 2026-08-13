# WestoryVisa 真实支付接入

支付代码已经按真实交易边界接入，但仓库和生产服务器目前没有商户账号、合同或密钥，所以线上必须保持“未接通”状态，不能伪造交易成功。

## 已实现

- 商品：月度会员 ¥199 / 30 天、年度会员 ¥1,990 / 365 天。
- 订单：创建本地订单后调用 Stripe Checkout，并保存网关单号和收银台地址。
- 回调：`POST /api/billing/webhooks/stripe` 使用原始请求体校验 `Stripe-Signature`，校验金额和币种，事件幂等后才开通会员。
- 查单：待支付订单可主动向 Stripe 查询并校准状态。
- 退款：已支付订单可调用 Stripe Refund API，全程记录本地退款状态。
- 数据隔离：订单、支付、退款和会员均绑定机构账号。

## 上线前必须由商户完成

1. 注册并完成 Stripe 商户审核；确认经营主体、结算账户、网站服务内容和适用地区符合要求。
2. 在 Stripe 中取得正式密钥，并把下列值只写入服务器 `/opt/docflow/deploy/backend.env`：

   ```dotenv
   BILLING_PUBLIC_BASE_URL=https://westoryvisa.com
   STRIPE_SECRET_KEY=sk_live_...
   STRIPE_WEBHOOK_SECRET=whsec_...
   ```

3. 在 Stripe 控制台登记 Webhook：

   `https://westoryvisa.com/api/billing/webhooks/stripe`

4. 至少订阅：`checkout.session.completed`、`checkout.session.async_payment_succeeded`、`checkout.session.expired`、`charge.refunded`、`refund.updated`。
5. 先使用测试密钥完成下单、回调验签、重复回调、查单、全额退款测试，再换正式密钥。

## 渠道说明

当前月度和年度方案是一次支付购买固定有效期，不声称自动续费。Stripe 的支付宝 Checkout 只支持一次性付款，不支持 subscription mode；如果需要境内微信/支付宝自动续费，必须先确定实际签约的商户渠道，再新增对应适配器和协议页面。

密钥、证书、合同、身份证明和结算资料不得提交到 GitHub。
