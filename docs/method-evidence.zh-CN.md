# LEDGER：MA5、成交量、VWAP、ATR5 与 gap 的证据边界

核验日期：2026-10-04。配套目录为 [sources.json](sources.json)，包含15项原始论文、作者稿和官方工程文档。文献用于解释研究依据与设计检验，不构成本项目绩效认证；本轮未引用未经核验的年化收益。

这套方法适合发展为可复现的教学研究系统。已有文献支持把趋势、量价关系和交易执行拆成可计算、可反驳的假说，但在本轮核验的来源中，没有发现对“MA5 + 简单ATR5 + −0.10至+0.20 ATR入场带 + 0.50 ATR gap上限 + 指定量能/VWAP条件”这个完整组合的直接学术验证。这里的“未找到”是本轮证据范围，不是声称所有文献中绝不存在相关研究。

证据不能跨层级自动迁移。BLL检验历史道指上的均线与突破，JT研究月度横截面赢家/输家，MOP研究跨资产自身历史收益趋势；这些都不是当代个股短周期回踩策略的复现。[S01](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1992.tb04681.x) · [S02](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1993.tb04702.x) · [S03](https://research-api.cbs.dk/ws/portalfiles/portal/58851003/time_series_momentum_lasse_heje.pdf)

“价格缓慢反映信息，趋势可能延续”可以作为机制假说；“回踩后必然继续涨”不能由前述研究推出。Lo、Mamaysky与Wang也明确区分了技术形态具有信息含量与交易策略可盈利。教学页面应让学习者先看到条件分布及反例，再看到加入仓位、退出和成本后发生什么。[S04](https://web.mit.edu/Alo/www/Papers/1705-1765.pdf)

| 系统元素 | 本轮能支持的解释 | 尚待本项目直接检验的部分 |
| --- | --- | --- |
| MA5、正斜率 | 趋势规则可形式化并与基准比较 | 5这个窗口、三点斜率、回踩后再上行的预测力 |
| 成交量/RVOL | 历史量能与动量持续、反转有关 | 日量比与同分钟RVOL不可互换；0.8或其他阈值没有被本目录直接验证 |
| VWAP | 可作为时段内价格位置或执行基准 | 站上/回踩VWAP是否带来增量收益；HLC3估计与真实逐笔量价结果的差别 |
| ATR5 | 用价格波动尺度表达距离，便于跨股比较 | 窗口、平滑方式、上下带、止损倍数的有效性 |
| gap过滤 | 把开盘偏离转为显式条件 | 过滤是否真正改善净期望，还是删掉了有利大波动 |
| 条件组合 | 可以预先冻结定义并做消融实验 | 组合是否增加信息、是否减少有效样本、结果是否稳定 |

成交量尤其需要反证。Lee与Swaminathan发现量能与动量生命周期相关，高量赢家也可能更快反转；这不支持“放量越大越可靠”。IBKR的VWAP文档则描述执行目标与不完全成交的取舍，没有提供预测alpha的证据。二者在系统里分别属于信号假说和执行语义。[S05](https://onlinelibrary.wiley.com/doi/10.1111/0022-1082.00280) · [S06](https://www.interactivebrokers.com/docs/general/order-types/algorithmic-orders/ib-algorithms/vwap)

研究设计须允许结论失败。Sullivan、Timmermann与White的结果并非一概否定技术分析：早期样本部分结果经数据窥探调整仍成立，但1987–1996年的样本外优势没有重复。这说明旧时期的统计显著性不能当作稳定性的替代品。[S07](https://www.kevinsheppard.com/files/teaching/mfe/advanced-econometrics/Sullivan_Timmermann_White.pdf)

PBO和DSR可帮助评估选择过程，却不是给回测盖章。前者需要多个候选的可比样本内/外结果；后者还需要试验依赖、样本长度及收益高阶矩。普通bootstrap置信区间不等于Reality Check，四段历史都为正不等于PBO通过，单个最优Sharpe也不足以完成DSR。未实现或缺少输入时，应显示“未计算”。[S08](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf) · [S09](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)

当前观察名单回看过去只能回答“这些已选股票的历史条件事件怎样”，不能回答“当时在全市场能选出什么”。Shumway说明退市终值遗漏本身就会影响可实现收益；Qlib的PIT文档说明后续修订值不能回填到当时的决策。股票身份、历史入池资格、财报公开时点与修订版本需要分别保留。[S10](https://onlinelibrary.wiley.com/doi/10.1111/j.1540-6261.1997.tb03818.x) · [S15/PIT](https://qlib.readthedocs.io/en/latest/advanced/PIT.html)

同样，次日开盘是明确的研究成交假设，不是保证成交。常数0/10/25/50bps往返成本适合压力比较；它没有重建价差、部分成交、市场冲击和容量。Almgren–Chriss把成本与执行风险共同建模，LEAN把成交、费用、滑点等拆开，Backtrader默认文档则直接说明了下一K线执行及默认不使用成交量约束的限制。[S11](https://www.risk.net/journal-risk/2161150/optimal-execution-portfolio-transactions) · [S12](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/key-concepts) · [S14](https://www.backtrader.com/docu/order-creation-execution/order-creation-execution/)

以下是据上述证据提出的本项目研究设计建议，不是这些论文已经替本项目完成的验证：

1. 为每个实验冻结规则、股票池、数据哈希、代码版本、信号时点、入场/退出、基准和成本；保存失败试验与变体总表。
2. 用一条可读时间轴区分“当时已知的数据”“信号形成”“假设成交”“未来观察标签”。按时间切分，明确跨切分持有窗口和重叠事件的处理。
3. 逐项去除MA斜率、距离带、gap、量能、VWAP，比较增量而非只比较最终收益；参数热图同时标注样本量、净效果、成本敏感性与样本外区间。
4. 相邻参数若频繁翻转结论，将其显示为脆弱证据；同日多股和重叠期限不能当独立观察。区间的统计方法及尚未调整的多重比较要紧邻结果展示。
5. 把事件研究与组合回测分为不同结果类型。没有逐日现金、持仓、并发和再投资路径时，不从事件均值推算CAGR、组合Sharpe或最大回撤。
6. 对资料不足显示具体原因。缺少20个此前完整交易日的同分钟量能，应是“RVOL基准不足”，不是“没有信号”；HLC3量价估计应明确标为近似。
7. 将未触发、失败、被过滤及无法评估的案例纳入教学；每项解释链接到对应规则、源数据和证据边界。

可借鉴的工程设计如下。此表不表示本轮已经安装这些项目或完成跨引擎验证。

| 官方项目 | 可借鉴的部分 | 要保留的限制 |
| --- | --- | --- |
| [vectorbt / S13](https://github.com/polakowo/vectorbt) | 多参数矩阵、交易明细、交互图 | 批量试验扩大选择空间；必须保留全部尝试 |
| [Backtrader / S14](https://www.backtrader.com/docu/order-creation-execution/order-creation-execution/) | 信号与订单的事件顺序 | 默认OHLC模型和量能假设须显式说明 |
| [LEAN / S12](https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/research-guide) | 时点数据、动态股票池、可替换现实模型 | 引擎不会修复输入的时点错误或过于乐观的成本 |
| [Qlib / S15](https://qlib.readthedocs.io/en/latest/component/recorder.html) | 实验/运行记录、参数与工件、PIT版本 | 使用功能还需要完整的原始数据与试验历史 |

本轮只读检查了已有 `research/study.py` 的定义，未运行或认证其中的绩效。该源代码用截至前一交易日已完成的信息生成滞后MA5与ATR5，ATR5为五个真实波幅的简单均值；三点OLS斜率 `(最后点−最早点)/2` 不代表三个点逐日单调。`daily_volume08` 使用当日完整成交量与此前20日平均成交量，其信息时点是收盘后，不能冒充同分钟RVOL。这些均为本项目实现定义，应随规则版本展示。

来源核验说明：目录中的 `year: null` 表示持续更新的官方文档没有可靠单一发布日期，不代表资料缺失；PBO按核验的2015作者稿标年，另保留出版页面。Wiley电子上线时间与纸本卷期年份可能不同，已按卷期纠正。所有转移边界均是对来源适用范围与本项目差异的分析推论，未将“没有直接证据”写成“已证明无效”。
