# Quant Detective · 可复现 MA5 研究实验室

这是研究和教学系统。你可以检查某笔实验为何入场、它占用了多少资金、费用如何扣除、扩展历史/换数据源后结论如何变化。系统不连接下单接口。

## 最快启动

打开本次交付的 `quant-detective-lab.html` 即可离线使用。切换数据源、规则、持有期和费用，只会选择真实计算完成的实验。结果不是滑杆拼凑的收益曲线。

从 GitHub 复跑代码：

```bash
git clone --branch research/ma5-lab-20261004 https://github.com/DropNowOfficial/quant-detective.git
cd quant-detective
uv sync --frozen --extra research
make verify
```

`make verify` 是唯一的核心验证入口。它验证合成反例和数学契约；没有原始行情时，不代表真实历史回测也被复现。

将授权快照包解压到仓库以外的绝对路径，例如 `/data/quant-detective-snapshots`，然后：

```bash
uv run --extra research qd-lab build \
  --ibkr /data/quant-detective-snapshots/ibkr \
  --yahoo /data/quant-detective-snapshots/yahoo \
  --sources research_outputs/sources.json \
  --review research_outputs/data-review.json \
  --expected-manifest research_outputs/snapshot-manifest.json \
  --output research_lab/runs

uv run qd-lab serve research_lab/runs/输出的实验ID
```

在终端显示的本机地址打开 `lab.html`。同一源码、Python/依赖环境和快照产生相同结果与实验 ID；`INPUT_MISMATCH` 表示输入已改变，不能继续称为这次实验的复现。Python版本也写入结果；本次市场计算环境见结果meta，跨版本可能发生浮点末位与ID变化。重新下载“10年”历史会移动窗口、变更known_at及历史修订，不保证相同指纹。

## 窗口与数据身份

- 原有 IBKR 快照：45文件，43只当前观察池股票和QQQ/SOXX，52,070根日线，2021年起多数有五年数据。
- 独立 Yahoo 二级快照：45文件、99,651根日线，最长从2016-10-03开始。当前名称及价格口径只作交叉验证，没有冒称完成CUSIP/历史身份认证。
- 主比较窗口：2022-01-03—2026-10-02，共1,192个XNYS交易日；另单列2017-01-03—2026-10-02的长历史。
- 151,721是两个来源原始行数合计，包含52,070个重叠symbol-session，不能当成151,721个独立样本。
- source + known_at + SHA256随每个源文件记录。known_at为快照取得时间，价格所属日期不是其历史可知时间证明。
- 结果snapshot_known_at表示最新源快照时间，不冒称构建页面的时间；构建时钟不参与确定性实验ID。
- 原始行情、嵌入行情的教学页面留在外部缓存；Git仅保存源码、合成测试、源清单/哈希、派生统计及审查。

## 固定主规则

信号日为t，所有量使用t收盘已完成或更早的信息。

| 项目 | 主规则 |
|---|---|
| MA5锚点 | mean(C[t−5]…C[t−1])，上一完成交易日的MA5 |
| TR | max(H−L, abs(H−前收), abs(L−前收)) |
| ATR5 | 前5个TR的算术平均；不是Wilder递归平滑ATR |
| 趋势 | 前日MA5一阶变化>0；三点OLS斜率≥0；不等于每天单调上涨 |
| 回踩 | (C[t]−MA5)/ATR5 ∈ [−0.10,+0.20] |
| 跳空 | abs((O[t]−C[t−1])/ATR5)≤0.50 |
| 操作门槛 | 价格≥$5、前20日平均成交额≥$20m、ATR/价格≤12% |
| 历史质量 | 61个连续完整股票交易日；QQQ相对收益窗口有效 |
| 触发 | 从不匹配变为匹配时进入队列；并非每个符合条件日重复建仓 |
| 排序 | 相对QQQ的20日收益差降序→此前成交额降序→代码 |

`completed_ma5` 单独将MA/ATR锚点推进到t收盘。`volume08` 单独加入日量比≥0.8。`trend` 只移除窄回踩带及gap限制，仍是3日等短持有实验；它不是文献中的12个月动量或长期趋势策略。

日线数据不能还原逐笔VWAP、同分钟RVOL或完整5分钟入场确认。历史5分钟缓存只有最多11个先前完整session，尚不能实现20日同时间量基线。缺少的盘中证据不被日线结果替代。

## 组合、年化与实际限制

初始$10,000；不加杠杆、不做空；最多5个不同标的。每笔预算上限为前收盘净值的20%，且包含买入费用并受现金与1%历史ADV名义容量上限约束。研究允许小数化价格单位，尚未模拟真实整数股份、分拆权益及现金结算限制。

t收盘锁定信号，t+1开盘入场；持有3session时在t+3收盘按预定规则退出。同一天晚些时候的卖出现金不能提前用于开盘买入。忽略未出现的信号，不回填错过的机会。

10bps往返成本拆为买卖各5bps，并按各自成交名义额扣除；0/25/50bps是完整敏感性矩阵。开盘/收盘价加固定成本并不保证实际成交。容量表只测试ADV限制，不是市场冲击或真实可管理资金认证。

CAGR=(末净值/初净值)^(365.2425/实际历日)−1。最大回撤包含初始资金；日波动率使用sqrt(252)；Sharpe以无风险利率0作简化。单笔平均收益、胜率和MAE/MFE不能直接当成年化。2026年度收益是截至10月2日的部分年度；独立重置子期与连续持仓分年收益分别保存。

QQQ/SOXX按同来源、同成本语义做持有对照。EW为当前池中已有61个有效历史交易日的标的，前日确定成员、次日开盘再平衡、计费，遇持仓缺价不会从分母消失。EW与QQQ风险暴露不同；同5仓/同持有/同费用的trend控制用于进一步拆解回踩规则贡献。

界面QQQ/SOXX/EW持有基准固定为10bps成本；逐笔账本、历史教学和统计反证固定为主规则/3日/10bps。切换其他费用或持有期只改变所选情形的净值与指标；不同设置不能称为匹配成本比较。

所有主结果都是价格收益，不含分红再投资、现金利息、税、借贷、准确执行冲击。price-return与total-return不可混用。源价格在拆股附近已表现连续，不能再次乘拆股比例。WDC/SNDK分拆、META从FB改名，以及SPCX/VRT部分日期差异仍是研究阻断点。

## 统计与反证

每个数据集完整显示4规则×4持有期×4费用，共64情形；3个数据集共192情形。另有43标的逐一剔除、固定子期重置、资金容量敏感性和数据源切换诊断。没有按最高年化选出“最优参数”。

配对20session循环区块重抽样保留相同时点的市场共振，1000次固定种子，给出年化日均差的描述区间。这不是CAGR之差的置信区间，也没有校正股票池选择和全部历史试验。QQQ回归提供RF=0的描述beta、年化截距和Newey-West区间；缺少多因子、真实RF、完整试验数，不能据此证明alpha。PBO/DSR明确保持未估计。

## 数据故障的处理

坏OHLCV整条标记，不手工推测high/low。入场决策不会提前查看持有期结果是否完整。出现无法认证的持仓收盘价时，保留最后可得估值、标记stale、暂停下一轮加仓，并只在后续可用收盘记录延迟退出；这是数据故障诊断路径，不是可交易的补救策略。`validated_metrics=null`，诊断数值另保留，不能称为可信绩效。

## 从研究走向实际使用

1. 现在先运行实验室和v0.1.1选股工作台，检查真实已完成RTH日线和每条筛选依据。授权快照取得之前不把旧价格当实时价格。
2. 冻结一个版本、成本、股票池和失败判据；开始前向影子记录。至少观察60个交易日并争取100个独立信号，属于操作验收目标，不是显著性保证。每次信号保存原始到达时间、修订、决策与实际可成交报价；不下单。
3. 前向期间不反复优化同一规则；若改规则，另开版本和试验账本。对预先确定的同风险/同成本控制检验净增量，区间下界不过0就不宣称优势。
4. 补全历史成分、退市、复权和分拆权益；取得至少20个先前完整session的盘中数据，闭合VWAP/RVOL/确认定义、缺价处理、仓位保护与退出。
5. 模拟执行中通过费用、点差、现金结算、断线恢复、重复信号去重及异常停机验收；由你事先明确最大可承受亏损与风险预算。本仓库保持研究only，实盘执行应由独立、隔离、另行授权的系统负责。

当前可以交付的是可审查研究工程和模拟交易前置体系。完整PIT数据库、多周期未见样本、真实成交验证与持续运维是实盘级的剩余工作，不能由一张高年化截图补齐。

## 独立数据审计复跑

```bash
uv run --extra research python scripts/audit_cross_source.py \
  --ibkr /data/quant-detective-snapshots/ibkr \
  --yahoo /data/quant-detective-snapshots/yahoo \
  --output /tmp/cross-source-audit.json
```

这份审计独立于组合引擎，重算45文件共同日期、价格差、坏OHLC和信号差。旧事件逐笔核验另用 `scripts/audit_legacy_snapshot.py --legacy-root 原v0.1.1完整包中的trade-monitor --output /tmp/legacy-audit.json`；该核验还需要旧包的事件表、原5分钟文件和请求清单，不只需要本次日线快照包。

## 浏览器验证

已安装Playwright及其Chromium的Node环境可运行 `node tests/browser_research_lab.cjs`（合成行为与安全反例），以及 `node tests/browser_market_lab.cjs research_lab/runs/实验ID/lab.html`（192组真实实验的界面数值与导出）。使用自有Chromium时设置 `CHROMIUM_EXECUTABLE`。浏览器验证不包含在 `make verify` 的Python门禁中；其实际运行环境和覆盖范围分别记入verification.json。
