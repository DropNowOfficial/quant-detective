"""Build a portable, self-contained evidence report from saved calculation outputs."""
import html
import json
import platform
from pathlib import Path
import numpy as np
import pandas as pd
from .study import ROOT, CORE, DISCOVERY


SOURCES=[
 ('Freqtrade — lookahead analysis','https://docs.freqtrade.io/en/stable/lookahead-analysis/',
  '改变未来数据、截断历史后重算，检查先前指标/信号是否变化。','已借鉴：43个标的、每个两个截点，共86项未来数据扰动与前缀检查。未声称运行了Freqtrade自身命令。'),
 ('Freqtrade — recursive analysis','https://docs.freqtrade.io/en/stable/recursive-analysis/',
  '改变启动历史长度，检查指标初始化依赖。','已借鉴：43个标的比较100根日线与完整历史的末端MA/ATR/主规则。VPR的历史分位数另有756观察值窗口，不能要求100根历史产生同样分位数。'),
 ('QuantConnect LEAN','https://github.com/QuantConnect/LEAN',
  '成熟的事件驱动、多资产回测框架；不是现成盈利策略。','建议作为完整股票组合/成交模拟的候选引擎。本轮未安装或整合LEAN，也未伪造跨引擎验证。'),
 ('LEAN — reality models','https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/key-concepts',
  '区分成交、滑点、手续费、买方能力及组合层约束。','本轮仅使用次日开盘研究入场与0/10/25/50bps往返成本压力；未模拟真实订单、冲击、排队或保证金。'),
 ('NautilusTrader — bar execution','https://nautilustrader.io/docs/latest/concepts/backtesting/bar-execution/',
  '完整K线只能在形成之后可见；OHLC不包含真实的盘内成交顺序。','已将信号日与入场日分开；盘中VWAP只标作HLC3估计。未来精确盘中模拟可评估该引擎，当前未整合。'),
 ('vectorbt — open source','https://github.com/polakowo/vectorbt',
  '适合向量化批量研究、参数矩阵与可检查的组合计算；开源项目与收费PRO产品需区分。','本轮保留三个预先定义的变体和全部结果，不只展示最好的一组。未安装vectorbt或借其名义认证结果。'),
 ('Bailey & López de Prado — Deflated Sharpe Ratio','https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf',
  '多次尝试、只展示赢家，会夸大回测表现；非正态与选择偏差需要单独处理。','记录129个标的×规则变体及5个期限，置信区间不作多重比较调整；不报告未经完整候选试验历史支持的DSR/PBO认证。'),
 ('Everpure — ticker change','https://www.everpuredata.com/company/newsroom/press-releases/everpure-to-change-ticker-symbol.html',
  '公司公告将NYSE代码由PSTG改为P，预计2026-04-17开始，CUSIP不变。','当前美国主上市合约已解析为P；旧PSTG查到的墨西哥上市结果未被混用。')]


def pct(x,d=2):
    return '—' if pd.isna(x) else f'{x*100:+.{d}f}%'


def render_table(headers,rows):
    return '<div class="table-wrap"><table><thead><tr>'+''.join('<th>'+html.escape(str(c))+'</th>' for c in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+html.escape(str(c))+'</td>' for c in row)+'</tr>' for row in rows)+'</tbody></table></div>'


def run():
    r=ROOT/'results';summary=json.loads((r/'summary.json').read_text());control=json.loads((r/'same_stock_control.json').read_text())
    intraday=json.loads((r/'intraday_summary.json').read_text());primary=pd.read_csv(r/'primary_3day.csv');quality=pd.read_csv(r/'data_quality.csv')
    events=pd.read_csv(r/'event_outcomes.csv');p=events[(events.variant=='primary')&(events.horizon==3)]
    folds=p[~p.fold_boundary_purged].groupby('fold').agg(n=('gross','size'),net=('net_10bps','mean'),excess=('excess_qqq','mean'))
    rows=[]
    for x in primary.to_dict('records'):
        rows.append([x['symbol']+('（原PSTG）' if x['symbol']=='P' else ''),x['n'],pct(x['mean_net_10bps']),
            pct(x['median_net_10bps']),f"{x['win_rate_10bps']*100:.1f}%",pct(x['mean_excess_qqq']),str(int(x['positive_folds']))+'/4',
            '不足30' if x['n']<30 else '仍未评级'])
    project_rows=[
      ['Core / Discovery日线历史层','已运行','43只股票；754个观察日历交易日；1/3/5/10/20日期限，逐笔结果、MAE/MFE、成本与时间分折。是日线代理研究。'],
      ['HARNESS V2完整盘中升级','受阻','45个标的各1000根5分钟线，12个完整时段；目标日最多只有11个此前完整时段，达不到20日同分钟RVOL。HLC3不是逐笔VWAP。有效完整HARNESS回测样本不可得，不能解释成策略无信号。'],
      ['Core延续突破 / momentum re-entry','规则未冻结','近1–3日阻力、偏好RVOL 1.1–1.2、例外和higher-low定义尚不唯一；未擅自补成可执行策略。'],
      ['AMD Profit Watch / 持仓退出','受阻','持仓保护比例、“及时收回”窗口及统一执行市场未定；原价位来自不同市场背景，不能拿美股日线代替OKX/Binance合约退出回测。'],
      ['1分钟均线合拢/缩量/方向放量','受阻','MA5/10/20与5/10/30、3根与4根缩量两版有差异；精确阈值和完整退出规则未找到。未将本轮股票代理冒充该策略。'],
      ['Quant Detective / 估值反推','受阻','当前包不含该项目源码、时点基本面和历史估值输入；此前审计保留DATA_PLANE_READY=NO。'],
      ['Flap MEME风险检测','受阻','缺历史交易流、地址状态、税费与流动性快照及结果标签；风险门槛不能直接当收益策略。'],
      ['Value–Price–Return Pendulum','部分完成','Price与5/20/60日Return历史分位数已算；真实历史基本面Value缺失，反向伸展只叫statistical value proxy，不作为入场规则。'],
      ['离线数据/状态/恢复/通知框架','已验证有限范围','修复7类回放缺陷；另修研究脚本2处边界错误。60次测试执行含继承重复，对应42个独立方法。仅本地日志接受，不等于通知送达。']]
    projects=render_table(['项目','本轮状态','证据与缺口'],project_rows)
    compare=render_table(['三日研究口径','均值 / 增量','说明'],[
      ['代理信号，扣10bps成本',pct(control['signal_net_10bps']),'1,196个完成且同股互不重叠的事件；不是账户组合收益'],
      ['同一批股票、无筛选条件',pct(control['same_stock_net_10bps']),'按信号数量加权的同股全部可分析日期；相同假设成本'],
      ['筛选相对同股的增量',pct(control['increment']),'95%日期块bootstrap区间 '+pct(control['ci_increment'][0])+' 至 '+pct(control['ci_increment'][1])+'，跨零'],
      ['相同日期相对QQQ的差值',pct(summary['mean_excess_qqq']),'1,185个可配对样本；等额同成本比较，成本抵消；不能据此分离选股、风险与择时优势']])
    costs=render_table(['往返成本假设','平均三日净收益'],[[f'{b} bps',pct(p[f'net_{b}bps'].mean())] for b in [0,10,25,50]])
    foldtable=render_table(['时间分折','完成且未跨折的样本','均值（10bps）','相对QQQ'],[[int(k),int(x.n),pct(x.net),pct(x.excess)] for k,x in folds.iterrows()])
    variants=render_table(['预先定义变体','三日样本数','均值（10bps）'],[[v,len(g),pct(g.net_10bps.mean())] for v,g in events[events.horizon==3].groupby('variant')])
    sources=''.join('<li><a href="'+html.escape(url)+'">'+html.escape(name)+'</a><p>'+html.escape(why)+' '+html.escape(use)+'</p></li>' for name,url,why,use in SOURCES)
    limitation=''.join('<li>'+html.escape(x)+'</li>' for x in [
      '当前股票池由今天的关注名单回看历史，并非每个历史时点可得的股票池；幸存者、选择偏差尚未消除。',
      '43个标的中25个的三日去重样本不足30；即使样本达到30，也不自动评级。SNDK、CRCL、ALAB、SPCX历史不足三年。',
      '置信区间使用20交易日时间块、2000次重采样、固定随机种子；合并结果保留同日跨股相关性。同股对照在每次重采样重算两端均值。均未进行多重试验选择校正。',
      '四折是事后按时间分段的稳定性检查，不是新数据上的样本外验证；没有用折内结果调参。',
      '52,070根原始日线中88根OHLC关系不一致，其中研究窗口内53根。原记录保留并隔离，依赖它们的指标或未来路径作缺失；没有修改高低价来凑过关。',
      '返回了部分公司行动事件，但复权政策未被接口明确说明。此处为价格收益，不含股息现金流，不宣称总回报。',
      'QQQ原始日期作为共同日历代理，尚未用独立交易所日历核验所有缺口；若同源同时漏一天，本轮不能排除。',
      '固定持有期限只是研究标签；未补造仓位、止盈止损、成交冲击、资金占用、真实手续费或执行规则。不得把逐事件均值串成组合年化收益。'])
    body=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>量化研究复核 · 2026-10-04</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f4f5f7;color:#18232f;font:16px/1.65 system-ui,-apple-system,BlinkMacSystemFont,"Noto Sans CJK SC",sans-serif}}main{{max-width:1120px;margin:auto;padding:48px 28px 80px}}h1{{font-size:36px;line-height:1.25;letter-spacing:-1px;margin:12px 0 20px}}h2{{font-size:23px;margin-top:42px}}p{{max-width:970px}}.eyebrow{{font-size:12px;letter-spacing:2px;color:#687785}}.lede{{font-size:20px;font-weight:550}}.box{{padding:22px 26px;background:white;border:1px solid #d8dfe5;border-radius:10px;margin:22px 0}}.warning{{border-left:5px solid #c38c26}}.metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}}.metric{{padding:20px;background:white;border:1px solid #d8dfe5;border-radius:8px}}.metric b{{display:block;font-size:32px;font-weight:650}}.muted,small{{color:#596978}}.table-wrap{{overflow-x:auto;background:white;border:1px solid #d8dfe5;border-radius:8px;margin:16px 0}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:11px 14px;border-bottom:1px solid #e5e9ed;text-align:left;vertical-align:top}}th{{background:#eaf0f4;white-space:nowrap}}tr:last-child td{{border-bottom:none}}code{{background:#e8edf1;padding:3px 6px;border-radius:4px}}a{{color:#1e5b86}}li{{margin-bottom:12px}}footer{{margin-top:44px;padding-top:20px;border-top:1px solid #d8dfe5;font-size:13px;color:#657380}}@media(max-width:650px){{main{{padding:26px 16px}}h1{{font-size:29px}}.metrics{{grid-template-columns:1fr}}th,td{{padding:9px 10px}}.box{{padding:18px}}}}@media print{{body{{background:white}}main{{padding:15px}}.table-wrap{{overflow:visible}}h2{{break-after:avoid}}tr{{break-inside:avoid}}}}
</style><main><div class="eyebrow">RESEARCH AUDIT / AS OF 2026-10-02 CLOSE</div>
<h1>数据已跑通，筛选优势仍未被证明。</h1>
<p class="lede">本轮完成43只股票的三年日线代理研究，并复核45个标的的五分钟数据。信号样本平均为正，但相对同一批股票不加筛选条件的增量，置信区间跨过零。</p>
<div class="box warning"><strong>结论边界</strong><p>这份结果支持继续研究，不支持把整套HARNESS标为已验证或据此自动交易。完整盘中规则、真实价值轴和独立持仓退出仍有缺口。全部Fit保持 <code>UNRATED_DAILY_PROXY</code>。</p></div>
<div class="metrics"><div class="metric"><small>完成的三日研究事件</small><b>1,196</b><small>同股互不重叠；跨股仍可同日发生</small></div><div class="metric"><small>三日均值 · 假设扣10bps</small><b>{pct(summary['mean_net_10bps'])}</b><small>胜率53.93%，中位数+0.41%</small></div><div class="metric"><small>相对同股无筛选基准</small><b>+0.14pp</b><small>95%区间−0.32pp至+0.59pp</small></div></div>
<h2>先看反证：收益来自哪里？</h2>{compare}<p class="muted">同股无筛选基准是新增的反证诊断：同时重采样信号收益与全部可分析日期收益，并重算两端均值。它控制当前股票池构成，仍不能消除所有时段/行情状态差异。没有因为结果不好而换参数。</p>
<h2>全部量化项目的覆盖范围</h2><p class="muted">本轮股票池为重建名单：找回的Core 22只，加此前明确增补的AAPL、CRCL、MSTR、SPCX；Discovery 17只（PSTG映射为P）。它不是对当前自动任务完整配置的重新核验，也不是历史时点股票池。</p>{projects}
<h2>样本如何定义</h2><p>MA5、ATR5只使用信号日之前已完成的日线，ATR5是五个真实波幅的简单均值；要求MA5日差大于零、三点OLS斜率非负，信号日收盘距MA5为−0.10至+0.20 ATR5，开盘跳空绝对值不超过0.50 ATR5。条件由不满足转为满足时记一次事件；收盘后才形成决定，次日开盘作假设入场，第1/3/5/10/20个后续交易日收盘观察结果。按期限分别去除同股重叠持有窗口。</p><p class="muted">这个日线代理没有验证盘中VWAP、同分钟RVOL或尚未形式化的流动性/突破例外。退出期限是研究标签，不是原有持仓退出规则。</p>
<h2>43只股票，逐项公开</h2><p>表中均为三日代理事件。正分折数不代表评级；样本少、真实盘中规则未验证、当时股票池未知，因此不展示“高适配”标签。</p>{render_table(['股票','样本n','均值净收益','中位数','胜率','相对QQQ','正分折','评级边界'],rows)}
<h2>成本、规则解释与时间稳定性</h2><p>成本是按本金扣减的总往返压力假设，不是IBKR实际费率。主规则提前固定；另外两种解释全部保留。</p>{costs}{variants}{foldtable}
<p class="muted">四段分别为2023-10-02–2024-07-02、2024-07-03–2025-04-03、2025-04-04–2026-01-02、2026-01-05–2026-10-02。跨折的10个三日结果只从分折统计中剔除，整体统计仍保留。</p>
<h2>AAOI：不是提高频率就能补上的漏报</h2><div class="box"><p>10月2日的78根常规时段五分钟线，<strong>0根</strong>收盘进入标准升级带；连K线范围也没有触及该带。此前完整日线给出MA5=101.094、ATR5=7.84598，升级带为100.309402–102.663196，而当天最低106.32。三点回归斜率+0.449并不等于三点逐日上涨。</p><p>该核查是把固定规则套回历史，不证明当时提醒任务实际用了同一版本。五分钟数据最多仅有11个此前完整交易日，仍不足20日RVOL基准。</p></div>
<h2>外部项目：学什么，实际做了什么</h2><p>Exa共返回33个搜索结果条目，随后对官方文档、原始仓库与论文定向核验。条目有重复，不能当33个独立支持者。成熟框架的存在也不证明任何策略赚钱。</p><ol>{sources}</ol>
<h2>数据与统计限制</h2><ul>{limitation}</ul>
<h2>验证与复现</h2><p>保留52,070根日线和45,000根五分钟线的原始响应、合约、请求信息和SHA-256。86项未来数据扰动/前缀检查和43项主指标预热检查通过；AAOI与已保存审计数值对账通过。独立审查发现的样本边界和恢复逻辑问题已修正。</p>
<p>解压后进入 <code>trade-monitor</code>，依次运行：</p><pre>python -m unittest discover -s tests -q
python -m research.study
python -m research.intraday_audit
python -m research.diagnostics
python -m research.build_report</pre>
<p>原离线框架仅依赖Python标准库；研究层另需NumPy/Pandas。运行环境：Python {platform.python_version()}、NumPy {np.__version__}、Pandas {pd.__version__}。数据读取均为只读；没有订单接口调用、付费行情订阅、部署或提醒任务变更。</p>
<p>下一道验收门槛：补齐逐标的至少21个完整常规时段的五分钟数据及可核对的VWAP口径；冻结突破例外和独立退出规则；以事先锁定的股票池和规则在新的未见数据中检验。原始参数、尝试数量和失败结果继续保留。</p>
<footer>2026-10-04 UTC · 研究窗口2023-10-02至2026-10-02 · 日线结构代理 ≠ 完整盘中HARNESS ≠ 实际组合收益</footer></main></html>'''
    (ROOT/'quant_audit_report.html').write_text(body)
    (ROOT/'sources.json').write_text(json.dumps([dict(name=n,url=u,principle=p,application=a) for n,u,p,a in SOURCES],ensure_ascii=False,indent=2)+'\n')
    (ROOT/'requirements.txt').write_text(f'numpy=={np.__version__}\npandas=={pd.__version__}\n')
    return ROOT/'quant_audit_report.html'


if __name__=='__main__':print(run())
