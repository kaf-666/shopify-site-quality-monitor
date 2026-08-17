# Phase 3.5A：Shadow Maturity

状态：已实现，仍为 **SHADOW ONLY**。新链 `affects_exit_code=false`，不会自动进入
ADVISORY，不修改 baseline、selector、站点配置或 SiteProfile。

## 1. Architecture Review

Phase 3 的主要瓶颈不是 executor 能否启动，而是 Legacy 观测和新 PlannedCheck
之间只有少量显式映射，并且 Add to Cart 的只读状态与真实动作共用一个
TRANSACTIONAL_SAFE capability。单次 smoke 因而只有约 45% mapping coverage，并
产生一次 policy regression。

Phase 3.5A 保留 Legacy Runner 为唯一 gate，在它完成后运行独立 browser context：

```text
Legacy Home / PLP / PDP
        │
        ├── legacy gate / exit code（保持不变）
        │
        └── SiteProfile → Planner → Generic Executors → CheckResult
                                      │
                                      ├── shadow-comparison.json
                                      ├── history/shadow/<site>.jsonl
                                      └── shadow-history-summary.json
```

Coverage matrix 的固定列为：Legacy identity、Capability、PlannedCheck、Executor、
Mapping Status、Execution Status、executable 和 gap。状态只允许 EXACT、PARTIAL、
MISSING、NOT_APPLICABLE；没有字符串相似度匹配。

## 2. Coverage Improvements

### Home

- navigation presence / visibility；
- 所有配置 core modules 的 attached/visible/layout 信号；
- main content 非空；
- hero visibility；
- 配置存在时的 DOM presence、plugin 和 mobile-menu trigger 信号。

### PLP

- product grid count 与 visibility；
- product-card presence；
- 抽样卡片 title、price、image descendant ratio；
- filter、sort、pagination control presence/visibility；
- 所有配置 core modules 与 DOM presence 信号。

### PDP

- title、price、gallery、main image；
- product info container、product form；
- variant / size / color selector presence（仅配置确认的 capability 执行）；
- 所有配置 core modules 与 DOM presence 信号；
- Add to Cart control readiness。

UNKNOWN capability 仍为 SKIPPED + UNVERIFIED，不以 PASS 填充 coverage。

## 3. Add to Cart Split

| Contract | Risk | 默认行为 | Executor |
|---|---|---|---|
| `commerce.add_to_cart.control_health` | SAFE | 执行 | `dom.control_state` |
| `commerce.add_to_cart.action_health` | TRANSACTIONAL_SAFE | POLICY_BLOCKED | 未绑定的显式 action contract |

Control 只读 presence、visibility、enabled/disabled、CTA 文案、busy/loading 状态，
并在结果中声明 `click_dispatched=false`。Action 只有显式 opt-in 后才可能进入 READY；
Phase 3.5A 的 MONITOR runtime 仍以 `transactional_safe_enabled=false` 运行，因此不会
真实加购。所有内建 HIGH_RISK capability 都不能被配置 override 降级。

## 4. Generic Executors

本轮新增三个通用 executor：

- `dom.multiple_signal_presence`：检查任意命名 selector 集合；
- `dom.descendant_presence`：抽样重复 root 并检查 descendant ratio；
- `dom.control_state`：只读 control readiness，不点击。

连同已有七个 executor，Registry 当前有 10 个通用实现。没有 Shopify 或站点专用
executor。

## 5. Mapping Semantics

- EXACT：新旧检查测量同一组配置 DOM 信号；
- PARTIAL：只覆盖视觉区域的语义健康、组合检查的一个分量，或导航已渲染证据；
- MISSING：没有显式语义关系或对应 PlannedCheck；
- NOT_APPLICABLE：Legacy 自身 SKIPPED，或旧空检查没有任何已配置 capability 信号。

视觉 PARTIAL 只贡献 coverage，不参与结果等价 parity。它们仍由原 Visual
Regression Engine、baseline、mask、stability 与 threshold 合同负责。Phase 3.5A
没有安全的轻量 adapter 可以复用完整 PageCheckContext，因此
`visual.region_snapshot` 明确保留为 MISSING；没有建立第二套 diff engine，也没有
创建或更新 baseline。

`overall_coverage_percent` 表示显式 semantic mapping；
`executable_coverage_percent` 只在一个 Legacy check 的全部映射关系均返回 COMPLETED
时计入。UNSUPPORTED、POLICY_BLOCKED、ERROR、TIMEOUT、SKIPPED 都不是成功执行覆盖。
Critical mapping 与 critical executable coverage 独立统计，并只以当前 Profile 中
PRESENT 的 HIGH/CRITICAL checks 为分母。

## 6. Real Desktop Smoke

2026-08-17 使用 `gracins_US` 的真实 Home、Collection、Product 页面完成 desktop
Legacy + Shadow smoke。Legacy gate 因已存在的结构/视觉差异和缺失 baseline 返回 1；
Shadow 仍完成并独立保存结果。映射规则稳定后又分别以 CODEX 和 HERMES metadata
运行两次真实 Shadow，算法和指标相同：

| Metric | Result |
|---|---:|
| Applicable Legacy checks | 24 |
| Planned checks | 52 |
| EXACT relationships | 6 |
| PARTIAL relationships | 44 |
| MISSING Legacy checks | 0 |
| NOT_APPLICABLE Legacy checks | 3 |
| Overall mapping coverage | 100.00% |
| Executable coverage | 100.00% |
| Critical mapping coverage | 95.83% |
| Critical executable coverage | 95.83% |
| Result parity | 100.00%（18 个等价样本） |
| Evidence parity | 100.00%（18 个去重 CheckResult 样本） |
| Policy regressions | 0 |
| Executor errors / timeouts / unsupported | 0 / 0 / 0 |

这些数字只描述本次站点、desktop、三页 scope，不代表所有站点已成熟。
同站点 mobile Home/PLP/PDP 也完成了真实 Legacy + Shadow 抽查，得到相同的
100% mapping/executable/result/evidence、95.83% critical coverage，以及 0 个
policy regression、executor error、timeout 和 unsupported；Legacy 因两个移动端
baseline 缺失保持 exit 1。

## 7. Shadow History

默认路径：`history/shadow/<site_id>.jsonl`。每次只追加一个 summary record，包含 run、
timestamp、site、page/viewport scope、coverage、parity、policy、error/timeout、
unsupported、flaky、mapping fingerprint、scheduler metadata 与 migration 状态。

不会保存 Observation、Finding、DOM、截图、trace 或性能数据。JSONL 被 gitignore；
run artifact 仅保存 `shadow-history-summary.json`。History 写入失败为 fail-open。

## 8. Consecutive Stability

稳定性只比较相同 page + viewport scope。当前 run 必须满足配置中的 coverage、result
parity、evidence parity、policy regression、executor error、timeout 与 mapping
fingerprint consistency；不要求所有业务 checks 都是 PASS。`legacy_gate_failed` 和
`scheduler` 不参与 stable 算法。

输出同时包含 `last_5_runs`、`last_10_runs`、窗口稳定率及
`consecutive_stable_runs`。mapping contract 发生变化时当前 run 会中断 streak；下一次
相同 fingerprint 才开始新的稳定序列。

## 9. Migration Readiness

阈值只在 `health_check.shadow_executor.maturity` 配置：overall 80、critical 90、
executable 80、result parity 98、evidence parity 95、policy/error/timeout 0、连续稳定
10 次。当前真实 smoke 的成熟度为 `SHADOW_MATURING`，稳定 streak 为 1；实际
`migration_status` 始终为 `SHADOW`。达到 10 次也只生成
`ADVISORY_CANDIDATE` / `recommended_readiness=ADVISORY`，不会自动晋级。

## 10. Remaining Gaps

- Add to Cart action executor 有意未启用；真实 cart/drawer item、variant、quantity
  验证留待显式 transaction 流程；
- `visual.region_snapshot` 未接入；
- Legacy pagination 的 link/overflow 细节、mobile drawer opened state 和 variant
  changed-state 仅有 PARTIAL semantic coverage；
- quantity、明确的 size/color、review、shipping、sort/load-more 只在 Profile 可靠
  确认后执行；
- Search、Cart page、Login、Account、Checkout、AI、Discovery、Performance、a11y、
  SEO、Alert、self-healing 均不在本阶段。

## 11. Operational Contract

- Shadow 默认关闭，可用 `--shadow-executor` 或
  `HEALTH_SHADOW_EXECUTOR_ENABLED=true` 显式启用；
- Scheduler 只记录 MANUAL/CODEX/HERMES/JENKINS metadata；
- `run-manifest.json` 声明 shadow history summary artifact；
- `run-summary.json` 与 stdout contract 提供 `history=` 路径；
- Jenkins 只归档 run 内的 history summary，不归档长期 JSONL；
- Legacy failure 与 Shadow maturity 互相独立。
