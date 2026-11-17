# Quant Detective 2.0 · 多因子横截面选股打分与三引擎回测（研究层）

状态：**实验研究实现（`DIAGNOSTIC_ONLY` / `NEEDS_BACKTEST`）**。
本模块严格遵守 `AGENTS.md` 硬性准则：不改动锁定的 `contracts/factors/FACTOR_CONTRACT_v1.md`，不向仓库提交任何原始市场行情，不触碰线上 `market_data/us_watch.py` 与自动下单，且所有诊断结果显式保留 `validated_metrics = None`（Vector 不自我认证 Alpha）。

## 1. 解决的核心结构性痛点

1. **打通孤岛因子原语（`research_lab/factor_mining/`）：**
   将原本独立在分支中的 PIT（时点无泄漏）原语合入研究主路径，并补齐基本面质量与微观结构原语：
   - **一步向前样本外残差动量（`residual.py`）：** 在交易日 $d$ 开盘前冻结前 $W$ 日对市场与行业超额收益的含截距 OLS 回归，计算样本外一步预测误差 $\epsilon_d = r_{i,d} - (\hat{\alpha} + \hat{\beta}_{\text{mkt}} r_{\text{mkt},d} + \hat{\beta}_{\text{sec}}(r_{\text{sec},d}-r_{\text{mkt},d}))$，通过 `Validation Kernel v0` 的 FM-003 截距一致性门禁。
   - **Amihud 非流动性（`amihud.py`）：** 20/60 交易日 $|r_t| / \text{DollarVolume}_t$，含次正规数防下溢缩放与 80% 会话覆盖率门禁。
   - **协方差谱有效维度（`dimension.py`）：** 特征值熵维度与参与度维度（Participation Dimension），用于量化股票池相关性拥挤与系统性共振。
   - **PIT 毛利资产比 GP/A、MAX 彩票股需求与 CLV 量价累积（`quality_microstructure.py`）：** 支持同一财报多科目证据去重、548 天陈旧度阻断、重述时点隔离，以及 `缺失 ≠ 0` 的严格处理。

2. **横截面多因子连续打分（`research_lab/selection_v2/scoring.py`）：**
   替代旧版仅按单一 20 日原始 `RS20` 排序的做法。对当日有效横截面计算并列均值百分位排名（0–100 分）：
   - 正向因子：特质残差动量（`residual_momentum`）、20 日成交量加权收盘强弱位置（`clv_accumulation`）、可选 PIT 毛利质量（`gross_profitability`）。
   - 负向排雷因子：21 日单日最大暴涨彩票度（`max_lottery`）、20 日冲击成本（`amihud_illiquidity`）。
   - 任何必需价格/微观结构因子缺失时，综合分保持 `None`（绝不填 0 或 50 分）。

3. **三引擎正交形态矩阵与市场状态门控（`research_lab/selection_v2/engines.py`）：**
   解决旧版单一 `-0.10..+0.20 ATR5` 窄带漏掉 `AAOI` 等跳空主升浪、同时在大盘暴跌日逆势接飞刀的问题：
   - **Engine A（`PULLBACK_V2`）：** 要求中期趋势多头排列（`Close > MA20 > MA50` 且 `MA20` 向上），回踩前日 `MA5` 期间出现 3 日缩量衰竭或当日阳线高位收盘。
   - **Engine B（`EPISODIC_PIVOT`）：** 捕捉跳空 $\ge 0.75\text{ ATR}_{14}$（或 $\ge 3\%$）、放量 $\ge 1.8\times$、突破 20 日前高且 `CLV >= 0.30` 的催化剂主升起点，同时自动过滤 `COHR` 类高开低走诱多假突破。
   - **Engine C（`VCP_BREAKOUT`）：** 识别波动率收敛（`ATR5 / ATR20 <= 0.75` 且近 10 日振幅 $\le 12\%$）后的放量箱体突破。
   - **Regime Gate：** 结合 `QQQ` 均线结构、全池站上 `MA20` 宽度与有效维度 `normalized_participation_dim`；在 `RISK_OFF` 暴跌期自动封锁回踩抄底。

4. **非对称移动止盈与子行业集中度约束组合（`research_lab/selection_v2/portfolio_v2.py`）：**
   - **ATR 波动率平价分仓：** 按单笔止损距离占前日净值固定风险预算（默认 `1.2%`）动态缩减高波标的仓位。
   - **8 大细分产业链上限（`max_per_sector=2`）：** 防止 5 个仓位全部涌入光模块或存储芯片单一细分赛道。
   - **严格因果次日开盘退出：** 收盘触发 `-1.5 ATR` 硬止损、最高收盘回撤 `2.5 ATR` 吊灯止盈、`MA10` 滞涨破位或最长持有上限后，统一于下一交易日开盘执行，杜绝日线最高/最低先后顺序的盘内偷价假设。

## 2. 四阶段受控消融实验入口（`ablation.py`）

在已准备好的外部快照 `Panel` 上直接运行 `S0 -> S1 -> S2 -> S3` 消融对比：

```python
from research_lab.data import load_panel
from research_lab.selection_v2 import run_ablation_matrix

panel = load_panel("/path/to/authorized-snapshots/yahoo", provider="yahoo")
report = run_ablation_matrix(panel, start="2022-01-03", end="2026-10-02")
print(report["ablation_run_id"], report["stages_by_cost"]["10bps"])
```
