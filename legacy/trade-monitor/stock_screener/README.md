# 美股量化选股工作台 0.1.1

0.1.1 修复未匹配列表的整行选择、焦点丢失、历史结果乱序覆盖、损坏响应处理、无效截止时间和负数 CSV 导出。现在可以点击行内任意位置，或用上下键、Home、End 切换个股。完整复现与压力测试记录见 `docs/2026-10-04-selection-bugfix.md`。

这是可运行的收盘选股工具：读取真实行情文件，校验数据，计算筛选条件，排序并保存证据。首版覆盖当前重建的 43 股观察池。它使用已下载行情；没有常驻自动行情源。盘中确认与策略收益优势仍待验证。

## 启动

在解压后的 `trade-monitor` 目录执行，推荐 Python 3.12 的独立环境：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m stock_screener serve
```

打开终端显示的 `http://127.0.0.1:8765`。macOS/Linux 可用以上激活命令；Windows 激活命令是 `.venv\Scripts\activate`。旧回放模块依赖 Unix `fcntl`，选股工作台不依赖该模块。

页面可搜索股票、切换分类、查看逐条条件、调整门槛、选择历史日期、导出当前列表、读取既有筛选。服务只监听本机地址。按 Ctrl+C 停止。

不启动服务，也可以直接打开交付的 `stock-selection-workbench-v0.1.1.html`：它可搜索、筛选、查看个股依据和导出，规则重算需使用上述服务。

## 命令行

```bash
# 使用当前时间对应的最新完整交易日
python -m stock_screener screen

# 固定截止时间，得到可复现结果
python -m stock_screener screen --as-of 2026-10-04T03:40:29Z

# 用当前下载的数据版本重建某个过去交易日
python -m stock_screener screen --session 2026-09-30

# 调整操作门槛（这些门槛与排序尚未单独验证收益）
python -m stock_screener screen --min-price 10 --min-adv20 50000000 --min-rs20 0

# 导入另一套同格式快照后计算
python -m stock_screener screen --raw /absolute/path/to/raw --output /absolute/path/to/runs
```

`--max-atr-fraction 0.12` 表示 12%；`--min-rs20 0.05` 表示相对 QQQ 至少 +5 个百分点。页面会自动换算百分比和百万美元单位。

## 如何使用结果

| 状态 | 意义 | 下一步 |
|---|---|---|
| 日线候选 | 通过数据、操作门槛和候选几何条件 | 人工检查事件风险及盘中条件 |
| 观察 | 趋势与宽区间通过，候选条件未完全通过 | 等待后续完整数据重新计算 |
| 未匹配 | 数据可计算，但当前规则不匹配 | 查看具体未通过的条件 |
| 数据阻断 | 数据或基准缺口使判断不可靠 | 先核验/补齐源数据 |

候选与观察组内按 RS20 相对 QQQ、前 20 日平均成交额、股票代码排序。序位不是买入评级或概率。负 RS 仍可能符合原日线几何；如要限制，可显式设置最低 RS20，不会暗中改动规则。

MA5 与 ATR5 使用前一交易日完成的数据；今日收盘用于观察距离，今日开盘用于跳空。候选区间 [-0.10,+0.20] ATR，观察区间 [-0.35,+0.40] ATR。所显示价格区间属于这次计算，下一交易日需要重算。日量比不是同时间 RVOL。

## 更新真实行情

每次重算都会重新读取 `--raw` 指定目录，但**不会联网下载报价**。默认目录包含上一轮取得的 IBKR 真实行情响应，截至 2026-10-02。交易日继续推进后，旧文件会触发过期阻断。

通过已授权的数据源取得完整的 RTH 日线，按 `research/raw/AMD_daily.json` 的封装保存。每只股票一个 `{SYMBOL}_daily.json`，必要字段如下：

- `symbol`，与 `stock_screener/universe.json` 一致。
- `contract.symbol`、`contract.underlying_contract_id`、`contract.exchange`，必须与股票池固定身份一致。
- `request.contract_id`、`request.security_type="STK"`、`request.step="ONE_DAY"`、`request.outside_rth=false`。
- `retrieved_at`：实际获取时间，必须带时区，且不早于所选交易日收盘后 30 分钟。盘中取得的未完成日线，不会因为之后时钟推进就自动变成完整数据。
- `data.chart_step=86400`、`data.source="Last"`、`data.time/open/high/low/close/volume` 等长数组。
- `data.time` 采用美股 RTH 开盘时刻，例如 `2026-10-02T13:30:00Z`，按时间升序，无重复。
- `data.corp_actions` 为供应商返回的公司行动数组，不能用空数组掩盖已知拆股。

个股至少提供 61 个连续交易日，基准 QQQ/SOXX 至少 21 日。默认包的早期响应缺少部分重复请求字段时，仅接受原始 SHA256 与 `research/request_manifest.json` 相符的补充约定；修改数据后必须补全请求字段，旧清单不能替新数据背书。

保留原始快照，核验新数据后使用单独的新目录运行最清晰。不要手工修改 high/low 去迎合 close。近期拆股且复权口径未知时会阻断；当前仍不能证明复权口径一致性。财报时间、估值与盘中数据未接入。

## 留档和验证

`screening_runs/<run_id>/` 包含 `result.json`、`screen.csv`、`workbench.html`。编号绑定全部结果、规则、股票池、源码、依赖版本及原始文件散列。已有文件冲突时拒绝覆盖。历史重算使用当前数据版本，不能冒充真正的当时留档；只有实际运行时保存的记录才可用于后续前向跟踪。

```bash
python -m unittest discover -s tests -q
```

完整方法、范围、外部参考与验收标准见 `docs/superpowers/specs/2026-10-04-stock-screener-design.md`；验证状态见本目录 `validation.json`。原始事件研究位于 `research/`，本次没有重新优化其参数。

## 接下来需要补齐

1. 可持续的日线增量数据源和复权核验，扩展有明确生效日期的股票池。
2. 为新增流动性门槛、RS 排序预先登记检验标准，积累前向记录。
3. 补足 20 个先前完整交易日的同时间盘中量数据，并核实精确 VWAP 与入场例外。
4. 仓位、保护与退出规则明确后，独立验证组合回报与交易成本。

原研究相对同股基线的增量区间跨零。本工具首先兑现可重复筛选与可审查证据，不能把这一进展当成已经证明盈利。
