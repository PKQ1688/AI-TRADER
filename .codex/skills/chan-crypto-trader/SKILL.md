---
name: chan-crypto-trader
description: Use when Codex needs Chan-theory-based cryptocurrency structure analysis for requests involving 缠论, 分型, 笔, 线段, 中枢, 背驰, 区间套, or 三类买卖点; require decision-only outputs in strict JSON plus concise Chinese conclusion, prioritize conservative confirmation logic, and never auto-place orders.
---

# Chan Crypto Trader

## Overview

执行基于缠论的加密市场结构分析，输出可执行但不自动下单的决策建议。
优先给出保守确认型结论，默认高一级别优先，低级别仅做确认或减仓辅助。

## Workflow

1. **数据校验**：校验输入字段和 K 线完整性。缺字段或样本不足（`bars_main < 50` 或 `bars_sub < 100`）时直接输出 `data_quality.status="insufficient"`。
2. **K 线包含处理**：对主级别和次级别分别执行 K 线包含关系处理（按时间顺序逐根合并，方向由前一根决定）。
3. **分型识别与笔生成**：
   a. 识别所有顶分型和底分型，标注分型强度（弱/强/最强）。
   b. 按同性质分型筛选规则（连续顶取最高、连续底取最低）过滤。
   c. 按笔规则（5 根最低要求、顶底配对）生成笔。
4. **线段生成**：基于笔提取特征序列，对特征序列做包含处理得到标准特征序列，按两种终结情况（无缺口/有缺口）判定线段终结或延续。
5. **走势中枢与走势类型**：在主级别识别最近走势中枢，计算 `[ZD, ZG]` 及 `GG/DD/G/D`，判断走势类型（盘整/趋势）和中枢演化状态（延伸/新生/扩展）。
6. **中阴阶段判断**：判断当前是否处于中阴阶段——若前一走势类型刚完成（背驰或盘整背驰确认终结）且新走势类型未确立，标记 `market_state.phase="transitional"`，借助前中枢进行分析。
7. **背驰与区间套**：在次级别判断背驰（a+A+b+B+c 模型）或盘整背驰，并通过区间套逐级缩小转折范围。MACD 作为辅助判断。
8. **买卖点生成**：按三类买卖点规则生成候选信号 `B1/B2/B3/S1/S2/S3`，每个信号标注结构前提、触发条件和失效条件。
9. **级别冲突与风控**：应用级别优先规则和保守确认规则，处理主次级别冲突，生成最终 `action.decision`。
10. **输出**：输出固定 JSON，并附一段简洁 `cn_summary`。

## Input Contract

### 必填字段

- `exchange`：交易所，例如 `binance`
- `symbol`：交易对，例如 `BTC/USDT`
- `timeframe_main`：主级别，默认 `1h`
- `timeframe_sub`：次级别，默认 `15m`
- `bars_main[]`：主级别 K 线数组，每项包含 `time/open/high/low/close`
- `bars_sub[]`：次级别 K 线数组，每项包含 `time/open/high/low/close`

### 可选字段

- `macd_main[]`：主级别 MACD 数据（DIF/DEA/柱值），用于背驰辅助判断
- `macd_sub[]`：次级别 MACD 数据

### 最低数据要求

- `bars_main[]`：至少 **50** 根 K 线（满足笔→线段→中枢的最低识别需求）。
- `bars_sub[]`：至少 **100** 根 K 线（次级别需要更细粒度结构用于区间套确认）。
- 不满足以上要求时，必须输出 `data_quality.status="insufficient"`，不得强行判断买卖点。

### 执行要求

- 不得在缺少主级别或次级别 K 线时强行判断买卖点。
- 不得在字段不完整时输出高置信度结论。
- MACD 缺失时仍可做纯形态学分析，但置信度上限降低 0.1。

## Output Contract

始终输出 JSON，固定字段如下：

```json
{
  "exchange": "binance",
  "symbol": "BTC/USDT",
  "timeframe_main": "1h",
  "timeframe_sub": "15m",
  "data_quality": {
    "status": "ok",
    "notes": ""
  },
  "market_state": {
    "trend_type": "up",
    "walk_type": "trend",
    "phase": "trending",
    "zhongshu_count": 2,
    "last_zhongshu": {
      "zd": 101000.0,
      "zg": 103200.0,
      "gg": 104500.0,
      "dd": 100200.0
    },
    "current_stroke_dir": "up",
    "current_segment_dir": "up"
  },
  "signals": [
    {
      "type": "B2",
      "level": "sub",
      "trigger": "一买后次级别回抽不破关键低点并重新转强",
      "invalid_if": "回抽低点跌破一买低点",
      "confidence": 0.72
    }
  ],
  "action": {
    "decision": "hold",
    "reason": "主级别未确认转折，次级别仅出现候选二买"
  },
  "risk": {
    "conflict_level": "low",
    "notes": "主级别与次级别方向轻微冲突，避免激进加仓"
  },
  "cn_summary": "当前以观察或轻仓持有为主，等待主级别确认后再执行加仓。"
}
```

### 字段规则

- `market_state.trend_type`：`up|down|range`
- `market_state.walk_type`：`consolidation|trend`（盘整/趋势）
- `market_state.phase`：`trending|consolidating|transitional`（趋势进行中/盘整中/中阴阶段过渡）
- `market_state.zhongshu_count`：当前走势中的中枢数量，`>= 0` 整数
- `market_state.last_zhongshu`：包含 `zd/zg/gg/dd` 四个数值字段
- `market_state.current_stroke_dir`：`up|down`（当前笔方向）
- `market_state.current_segment_dir`：`up|down`（当前线段方向）
- `signals[].type`：`B1|B2|B3|S1|S2|S3`
- `signals[].level`：`main|sub`
- `action.decision`：`buy|sell|reduce|hold|wait`
- `risk.conflict_level`：`none|low|high`
- `confidence`：`0.0-1.0`

## Decision Policy

执行以下固定优先级：

1. **高级别优先于低级别**。主级别趋势延续时，低级别反向信号仅用于 `reduce` 或短差，不用于重仓反转。
2. **保守确认型**：默认优先 `B2/B3` 或 `S2/S3`。`B1/S1` 仅作为候选，不直接给重仓结论，需次级别确认后方可升级动作。
3. **同时出现多信号时**，以结构完整度和失效条件清晰度更高者优先。
4. **中阴阶段处理**：
   - 中阴阶段内默认 `action.decision = "wait"`，除非中枢震荡给出明确的第三类买卖点。
   - 中阴阶段内所有信号的置信度上限为 `0.6`。
   - 中阴阶段结束的标志：新走势类型的第三类买卖点出现。
5. **走势类型延伸处理**：
   - 盘整延伸中：中枢形成后走势不断回到中枢，不产生新中枢 → 按中枢震荡处理，关注 Zn 变化。
   - 趋势延伸中：同向走势不断产生新中枢 → 注意每个新中枢形成后的背驰判断（a+A+b+B+c 逐段比较）。
   - 走势类型延伸随时可能结束，关键在于是否产生新的走势中枢。
6. **同级别分解说明**：
   - 可选的机械化操作方法：按固定级别分解走势为 A0+A1+A2+...，比较 Ai 与 Ai+2 的力度。
   - 适合不区分牛熊、按固定节奏操作的场景。
   - 详见 `references/chan-core-rules.md` 第 2.11 节。

## Safety Rules

- 仅做分析，不调用交易接口，不自动下单。
- 禁止输出"必涨""必跌""稳赚"等情绪化或确定性承诺。
- 每个信号必须包含 `trigger` 与 `invalid_if`。
- 冲突高时优先 `wait` 或 `reduce`。
- 中阴阶段默认 `wait`，除非有明确的中枢震荡短差机会。
- 极端波动后（闪崩/暴拉），优先标注 `risk.conflict_level = "high"` 并建议 `wait`。

## References

- 核心规则与判定细节：`references/chan-core-rules.md`
- 输出格式与错误码：`references/signal-output-spec.md`

按需加载参考文档，不在主文档重复长篇定义。
