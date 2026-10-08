# Quant Detective：系统地图与入口

先按要做的事选入口。这个仓库同时保存三组工作：**旧 ChatGPT HARNESS 相关工作、研究与回放、实时观察**。实时观察里又有浏览器、VPS 和 GitHub 三条独立运行链路，不能用一条的成功证明另一条正常。

本文按 `main` 的 `dc9ca5e222e8514ecc315b5e65fea4380df3a122` 核对（2026-10-05 UTC）。这是代码与配置快照，**不是主机在线、部署成功或通知送达证明**。后续变化以对应源码及实际运行证据为准。

## 1. 我要做什么？

- **看当前分钟行情和筛选解释**：从仓库根目录运行 `python -m market_data serve`，打开 `http://127.0.0.1:8767/`。保持服务和浏览器轮询；[LIVE 说明](live-screener.zh-CN.md)
- **手动查一份原始 K 线**：同一服务的 `http://127.0.0.1:8767/market`。这是按请求查询，不是常驻扫描
- **看已有研究结论**：同一服务的 `http://127.0.0.1:8767/research` 读取冻结摘要；完整复现实验和教学界面走 [研究指南](research-guide.zh-CN.md)
- **回看浏览器刚才的扫描**：LIVE 中的 REPLAY，仅保存会话内最近30个批次；不是持久交易账本或研究回测
- **查原来选股工作台与旧日线研究**：[legacy/trade-monitor](../legacy/trade-monitor/README.md)。它是保留的旧实现，不是完整 HARNESS 已验证或自动交易的证明
- **查无人值守服务是否健康**：看 VPS 的有效配置、当前 release、systemd 和最新 state；[主机指南](self-hosted-ibkr-watch.md)
- **查 GitHub 扫描有没有执行、有没有发出评论**：看 [fallback Actions](https://github.com/DropNowOfficial/quant-detective/actions/workflows/market-watch.yml) 和当日 `Market Watch | YYYY-MM-DD ET` issue；[扫描与通知说明](continuous-market-watch.md)

首次取代码使用 `main`，不要把旧 feature 分支当日常入口。Web 服务需要 Python 3.11+ 与 curl；Windows 可运行根目录 `start-live.cmd`。这一步只启动本机 Web 服务，不会安装 VPS 服务或开启 GitHub 调度。

## 2. 组件、边界与状态

下表的“状态”只表示核对到的仓库能力。实际运行状态需要最后一列的证据。

| 分组 / 组件 | 入口与源码依据 | 当前能力与生命周期 | 不能据此声称 / 运行时要核对 |
|---|---|---|---|
| 旧 HARNESS 相关：保留工作台 | [旧工作台](../legacy/trade-monitor/README.md)，在该目录运行 `python -m stock_screener serve --raw /absolute/path/to/authorized/ibkr` | 旧选股与冻结日线事件研究；需要获授权快照 | 不等于完整原始盘中 HARNESS 已验证；不能把日线代理收益换成分钟策略收益 |
| 研究与回放：研究实验 | [研究指南](research-guide.zh-CN.md)、[research_lab](../research_lab)、[研究摘要](../research_outputs/summary.json) | 冻结日线、有限现金组合、情景与数据审查；复现需固定快照 | 不代表实时信号、稳定 alpha 或交易许可；查看数据状态与验证范围 |
| 研究与回放：研究 OS | [架构](architecture.md)、[contracts](../contracts)、[validation_kernel](../validation_kernel)、[time_machine](../time_machine) | 保留证据合同、确定性校验及 PREDICT → LOCK → REVEAL → SCORE | 架构图不是各运行进程已接通的证明 |
| 实时观察：浏览器 LIVE | `8767/`；[路由](../market_data/server.py)、[会话扫描](../market_data/live.py) | `MINUTE_MA5_V1` 分钟规则；目标12秒一批；会话超过90秒无访问会被移除 | 不是旧日线 HARNESS；服务存活不等于扫描仍持续；检查页面来源、时效和覆盖 |
| 实时观察：手动行情 | `8767/market`；[路由](../market_data/server.py) | 手动请求公开 K 线；一次请求一次结果 | 不持续盯盘、不推送 |
| 实时观察：VPS daemon | `ibkr-watch`；[service](../deploy/systemd/quant-detective-live.service)、[daemon](../market_data/ibkr_live.py)、[示例配置](../deploy/systemd/market-watch.env.example) | 预期常驻；默认2秒 IBKR 快路径、60秒结构路径；示例41只核心池，新增 IBKR 美股主要市场 Top-N 发现候选（默认最多24个送入股票类型筛选及结构分析）；写 `state.json` / `events.jsonl` | 发现源码见 [market_discovery](../market_data/market_discovery.py)；`discovery_exhaustive=false`，不是全市场逐只全覆盖；实际范围/频率及发现可用性要看主机状态；IBKR 不可用会降级；当前未调用 GitHub publisher，不证明通知送达 |
| 实时观察：GitHub fallback | [workflow](../.github/workflows/market-watch.yml)、[public watch](../market_data/us_watch.py)、[publisher](../market_data/github_alerts.py) | 独立公开数据扫描；配置五分钟时段调度，每次 `--once --github-alerts`；当日 issue 的心跳和去重事件评论 | 不是 VPS 输出转发；push 成功不等于 schedule 成功；声明 cron 不证明实际执行，评论不证明终端收到通知 |
| 运维：更新与看门狗 | [updater](../deploy/update-production.sh)、[update timer](../deploy/systemd/quant-detective-update.timer)、[watchdog](../deploy/watchdog.py) | 当前 updater 配置为上次服务结束后60秒再触发，另有精度/随机延迟；更新脚本包含同版本 watchdog 协调 | 合并不等于主机已安装；timer 不等于行情刷新频率；核对 active release、有效 units、最新健康数据 |

## 3. 三条实时链路如何区分

1. **浏览器**：页面轮询 → 本机 LIVE 会话 → 公开源分钟数据 → 页面状态/内存回放。页面停轮询会导致会话失效
2. **VPS**：systemd → `ibkr-watch` → IBKR 快路径与 Top-N 候选发现 + 公开结构路径 → 本机 state/events 文件。没有连接到 GitHub publisher 的调用
3. **GitHub**：schedule / 指定文件的 push / 手动触发 → `watch --once --github-alerts` → 独立扫描 → 当日 issue 的心跳与事件评论

三者不是同一行情结果的三个显示窗口。`READY`、`ENTRY_ARMED`、`ENTRY_CONFIRMED` 等状态来自不同规则路径，不能互换。行情条数、扫描中的 material alerts 数、去重后新评论数、实际收到的通知数也不能混算。

## 4. 怎样判断“真的在工作”

- **代码层**：确认分支和完整 SHA；PR、计划文件、合并记录只证明开发状态
- **主机层**：确认 `/opt/quant-detective/current` 指向哪个 release、服务是否活跃、有效 timer/覆盖配置是否一致
- **数据层**：确认最新时间、实际 mode、来源、结构数据年龄、逐标的错误与覆盖；`DEGRADED_PUBLIC_ONLY` 不能写成 IBKR 实时报价正常
- **发布层**：确认具体 Actions run 的 trigger/commit/conclusion，以及当日 issue 心跳和实际新增评论
- **送达层**：确认接收端记录。发布成功与用户收到是两步

`make verify` 是仓库规范验证入口；离线测试通过不证明数据源可达、主机部署或通知送达。原有 [AGENTS 规则](../AGENTS.md)、HARNESS 边界及反泄漏约束保留：缺失不等于零，研究不自动授权交易。

本页只整理入口、生命周期和证据边界，不迁移目录、不停止服务、不修改规则、计时器或部署。
