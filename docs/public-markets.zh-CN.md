# 多市场公开 K 线终端

该工具从各来源实际读取品种目录，搜索代码或名称后，手动获取一次公开 K 线。不是固定的 SOLUSDT 示例，也不把合约行情当成现金股票。只读、无密钥、无账户连接、无订单和交易信号。

## 启动

需要 Python 3.11+、uv 和 curl。

```bash
git clone --branch research/ma5-lab-20261004 https://github.com/DropNowOfficial/quant-detective.git
cd quant-detective
uv sync --frozen
uv run qd-market serve
```

打开 `http://127.0.0.1:8767/`。选择市场，等待公开目录返回，搜索并选择品种，然后点击“获取一次 K 线”。图表支持鼠标和方向键查看逐根 OHLCV，表格支持排序和分页。切换市场、品种、周期或请求根数会清除旧价格，防止把旧响应误当作当前选择。

CLI 可独立读取目录或指定品种；默认仍遵循来源 A、SOLUSDT、1m、5 根，但不会限制可选品种。

```bash
uv run qd-market catalog --market gate_usdt
uv run qd-market candles --market binance_spot --symbol ETHUSDT --interval 5m --limit 5
uv run qd-market candles --market gate_usdt --symbol BTC_USDT --interval 15m --limit 5
uv run qd-market candles --market us_equity --symbol AAPL --interval 1m --limit 5
uv run qd-market candles --market cn_equity --symbol 600519 --interval 5m --limit 5
uv run qd-market candles --market okx_swap --symbol BTC-USDT-SWAP --interval 1m --limit 5
uv run qd-market candles --market binance_usdm --symbol BTCUSDT --interval 1m --limit 5 --json
```

## 覆盖与已观察到的限制

以下为 2026-10-04 UTC 的测试记录，不保证以后可用，也不是全品种历史完整性认证。完整请求时间、HTTP 状态及来源见 [公开来源验收记录](../research_outputs/public-markets.json)。

| 市场参数 | 品种目录与 K 线来源 | 已验证状态及边界 |
|---|---|---|
| `binance_spot` | 来源 A：Binance 现货公开镜像 | 目录返回 1,372 个活动品种，实际公开 K 线读取成功；仅此来源显示 index 9 的主动买量 |
| `gate_usdt` | 来源 B：Gate USDT 永续 | 目录返回 1,027 个合约；BTC、ETH、XAU 的允许周期样本读取成功；成交量为合约张数，没有主动买量 |
| `okx_swap` | OKX 永续 | 已实现官方目录和 K 线格式；当前网络返回 HTTP 200 的拦截 HTML，按失败处理 |
| `okx_futures` | OKX 交割 | 同上；不冒充永续或现货，不用其他来源替代 |
| `binance_usdm` | Binance U 本位合约 | 当前 K 线请求 HTTP 451；如实报告限制 |
| `binance_coinm` | Binance 币本位合约 | 当前 K 线请求 HTTP 451；保留自身品种和原始量口径 |
| `us_equity` | Nasdaq Trader 官方上市证券目录；Yahoo 分钟 K 线 | 目录 13,258 个非测试证券，含 ETF 等，并非全部 OTC；分钟样本曾成功，后续请求出现 HTTP 429 |
| `cn_equity` | 巨潮 A 股披露搜索目录；Yahoo 分钟 K 线 | 目录 6,179 个唯一代码，含历史、退市或改码，所有上市状态均未认证；沪深样本曾成功，后续出现 429；北交所样本 404，不能宣称分钟覆盖 |

Gate 的股票、黄金或指数类条目仍是 Gate 永续衍生合约，不能代替对应现金市场价格。A 股目录显示为不完整覆盖，因为“可搜索到代码”不等于“当前上市且分钟行情可用”。本工具没有凭公开披露搜索目录构造历史可交易股票池。

## 数据契约

- 周期仅 `1m`、`5m`、`15m`。一次用户操作是有限批量读取，不是连续刷新。没有自动轮询。
- 每个上游 GET 的总时限为 12 秒；超时停止，非 200 保留状态码；200 但不是可解析行情同样失败。无自动重试、重定向、代理替换或跨源补造。
- UI / CLI 单批为 1–100,000 根，每页不超过 1,000 根；OKX 每页最多 300。来源 A 用最早开盘毫秒减 1 作为 `endTime`；来源 B 按用户协议固定 `limit=1000`，用最早 `t` 减 1 秒分页。Gate 官方参数描述与这一组合存在差异，实测成功不代表永久支持。
- Yahoo 首页请求最近一个交易日，避免周末和长假猜错日期；后续时间窗口最多覆盖 1,000 个周期。有限历史、空页、无效行或限流可能造成不足请求根数，明确标记部分结果。不会用零填补。
- 丢弃非有限 OHLCV、负成交量、最高价低于最低价或开收盘超出高低区间的行；排序、去重。未收盘按请求时间和周期结束时间判断；OKX 同时核验 `confirm`。
- 仅来源 A 使用提供的主动买量；来源 B 明确没有该字段。其他来源即使另有成交分类，也不会擅自映射为本工具的主动买量。
- 股票保留正常交易时段起点，排除 A 股午休与 Yahoo 每日收盘终值点；逐日节假日与半日市日历尚未认证。界面显示实际 UTC 开盘时间，不把最近交易日价格叫作现在的价格。
- 股票行情核验返回资产身份；缺少身份信息或跨市场时失败。当前美国报价身份仅认证 NMS / NGM / NYQ 的 EQUITY / ETF、纽约时区和 USD；其他交易场所目录仍显示，但报价明确返回未认证。沪深分别核验 SHH / SHZ、EQUITY、上海时区和 CNY。目录存在不保证每个品种都能通过该来源的身份和数据检查。
- 股票来源可能延迟；Yahoo 公开分钟端点没有经本项目验证的延迟 SLA。成交量单位未认证时显示原始字段，不跨源比较。

浏览器取消后，已发出的 GET 最多完成其 12 秒时限，服务器不再发后续页。客户端对整批等待也设上限；很长的分钟历史应拆成来源允许的批次。公开读取权限不等于数据再分发授权，因此 Git 中只放代码、合成测试、来源与验收元数据，不放原始市场数据。

## 可复查来源

- [Binance 现货公开市场数据说明](https://github.com/binance/binance-spot-api-docs/blob/master/market_data_only.md)
- [Binance USD-M K 线](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Kline-Candlestick-Data)
- [Binance COIN-M K 线](https://developers.binance.com/docs/derivatives/coin-margined-futures/market-data/rest-api/Kline-Candlestick-Data)
- [OKX 官方公开目录与 K 线 API](https://www.okx.com/docs-v5/en/)
- [Gate 永续 API](https://www.gate.com/docs/developers/apiv4/en/futures/)
- [Nasdaq Trader 证券目录定义](https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs)
- [巨潮公开披露搜索目录](https://www.cninfo.com.cn/new/data/szse_stock.json)
- [Yahoo 各市场数据延迟说明](https://help.yahoo.com/kb/finance/article-exchanges-data-delays-sln2310.html)

## 与研究实验室的关系

这是行情观察和数据质量检查入口。它没有启用 MA5 或其他交易信号。原有研究实验室保留既有冻结实验，详见 [研究与启动说明](research-guide.zh-CN.md)。新的市场和分钟数据不会自动继承既有日线回测结论；完整历史证券池、复权与资金费率、缺口处理、逐日交易日历、费用、未参与调参的样本外检验仍是下一阶段研究条件。
