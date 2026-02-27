# Signal Output Specification

## 1. 目标

定义 `chan-crypto-trader` 的输入和输出契约，确保结果可被程序解析并可用于人工复核。

## 2. 输入契约

### 2.1 必填字段

- `exchange`：字符串，例如 `binance`
- `symbol`：字符串，例如 `BTC/USDT`
- `timeframe_main`：字符串，默认 `1h`
- `timeframe_sub`：字符串，默认 `15m`
- `bars_main[]`：数组，元素必须包含 `time/open/high/low/close`
- `bars_sub[]`：数组，元素必须包含 `time/open/high/low/close`

### 2.2 可选字段

- `macd_main[]`：主级别 MACD（DIF/DEA/柱值），用于背驰辅助判断
- `macd_sub[]`：次级别 MACD

### 2.3 最低数据要求

| 字段 | 最低数量 | 说明 |
|------|---------|------|
| `bars_main[]` | **50** 根 | 满足笔→线段→中枢的最低识别链路 |
| `bars_sub[]` | **100** 根 | 次级别需要更细粒度用于区间套确认 |

- 任一关键字段缺失或不满足最低数量时，返回 `data_quality.status="insufficient"`。
- MACD 缺失时仍可做纯形态学分析，但所有信号的置信度上限降低 0.1。

## 3. 输出契约

### 3.1 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `exchange` | string | 是 | 交易所名称 |
| `symbol` | string | 是 | 交易对 |
| `timeframe_main` | string | 是 | 主级别时间框架 |
| `timeframe_sub` | string | 是 | 次级别时间框架 |
| `data_quality` | object | 是 | 数据质量评估 |
| `market_state` | object | 是 | 市场结构状态 |
| `signals` | array | 是 | 买卖点候选信号列表 |
| `action` | object | 是 | 最终决策 |
| `risk` | object | 是 | 风险评估 |
| `cn_summary` | string | 是 | 中文简要结论 |

### 3.2 `market_state` 字段详情

| 字段 | 类型 | 说明 |
|------|------|------|
| `trend_type` | string | 当前趋势方向 |
| `walk_type` | string | 走势类型（盘整/趋势） |
| `phase` | string | 当前阶段（趋势进行中/盘整中/中阴过渡） |
| `zhongshu_count` | integer | 当前走势中的中枢数量（>= 0） |
| `last_zhongshu` | object | 最近中枢的四个关键指标 |
| `last_zhongshu.zd` | number | 中枢下界 ZD = max(d1, d2) |
| `last_zhongshu.zg` | number | 中枢上界 ZG = min(g1, g2) |
| `last_zhongshu.gg` | number | 中枢最高点 GG = max(gn) |
| `last_zhongshu.dd` | number | 中枢最低点 DD = min(dn) |
| `current_stroke_dir` | string | 当前笔方向 |
| `current_segment_dir` | string | 当前线段方向 |

### 3.3 枚举约束

| 字段 | 允许值 |
|------|--------|
| `data_quality.status` | `ok` \| `insufficient` |
| `market_state.trend_type` | `up` \| `down` \| `range` |
| `market_state.walk_type` | `consolidation` \| `trend` |
| `market_state.phase` | `trending` \| `consolidating` \| `transitional` |
| `market_state.current_stroke_dir` | `up` \| `down` |
| `market_state.current_segment_dir` | `up` \| `down` |
| `signals[].type` | `B1` \| `B2` \| `B3` \| `S1` \| `S2` \| `S3` |
| `signals[].level` | `main` \| `sub` |
| `action.decision` | `buy` \| `sell` \| `reduce` \| `hold` \| `wait` |
| `risk.conflict_level` | `none` \| `low` \| `high` |

### 3.4 数值约束

- `signals[].confidence`：`0.0 <= value <= 1.0`
- `market_state.zhongshu_count`：`>= 0` 整数
- `last_zhongshu.zd/zg/gg/dd`：数值型，`zd <= zg`，`dd <= gg`

### 3.5 置信度校准标准

| 置信度区间 | 含义 | 条件 |
|-----------|------|------|
| `0.8 - 1.0` | 结构完整，已确认 | 结构完整，次级别已确认，MACD 辅助一致 |
| `0.5 - 0.8` | 部分形成，等待确认 | 结构部分形成，等待次级别确认中 |
| `0.0 - 0.5` | 仅候选 | 结构不完整或存在冲突，仅为候选 |

特殊限制：
- 中阴阶段（`phase="transitional"`）内的信号，置信度上限不超过 `0.6`，除非中枢震荡给出明确的第三类买卖点。
- MACD 缺失时，所有信号置信度上限降低 `0.1`。

## 4. 标准 JSON 模板

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
    "trend_type": "range",
    "walk_type": "consolidation",
    "phase": "consolidating",
    "zhongshu_count": 1,
    "last_zhongshu": {
      "zd": 100000.0,
      "zg": 101500.0,
      "gg": 102800.0,
      "dd": 99200.0
    },
    "current_stroke_dir": "up",
    "current_segment_dir": "up"
  },
  "signals": [
    {
      "type": "B3",
      "level": "sub",
      "trigger": "向上离开中枢后首次回抽不回中枢并转强",
      "invalid_if": "回抽重新跌回中枢区间",
      "confidence": 0.68
    }
  ],
  "action": {
    "decision": "hold",
    "reason": "主级别未形成明确突破，次级别仅给出延续信号"
  },
  "risk": {
    "conflict_level": "low",
    "notes": "主次级别方向未完全一致"
  },
  "cn_summary": "当前以持有和观察为主，等待主级别确认突破后再考虑加仓。"
}
```

## 5. 数据不足模板

```json
{
  "exchange": "binance",
  "symbol": "BTC/USDT",
  "timeframe_main": "1h",
  "timeframe_sub": "15m",
  "data_quality": {
    "status": "insufficient",
    "notes": "bars_main 仅 30 根，不足最低要求 50 根；bars_sub 缺失"
  },
  "market_state": {
    "trend_type": "range",
    "walk_type": "consolidation",
    "phase": "consolidating",
    "zhongshu_count": 0,
    "last_zhongshu": {
      "zd": 0,
      "zg": 0,
      "gg": 0,
      "dd": 0
    },
    "current_stroke_dir": "up",
    "current_segment_dir": "up"
  },
  "signals": [],
  "action": {
    "decision": "wait",
    "reason": "数据不足，停止强判"
  },
  "risk": {
    "conflict_level": "high",
    "notes": "输入不完整，结论不可执行"
  },
  "cn_summary": "当前数据不足，先补齐主次级别K线后再分析。"
}
```

## 6. 中阴阶段模板

当 `market_state.phase = "transitional"` 时的参考模板：

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
    "trend_type": "range",
    "walk_type": "consolidation",
    "phase": "transitional",
    "zhongshu_count": 1,
    "last_zhongshu": {
      "zd": 99500.0,
      "zg": 101000.0,
      "gg": 101800.0,
      "dd": 98800.0
    },
    "current_stroke_dir": "down",
    "current_segment_dir": "up"
  },
  "signals": [
    {
      "type": "B3",
      "level": "sub",
      "trigger": "次级别向上离开前中枢后回抽不回中枢",
      "invalid_if": "回抽重新跌回前中枢区间",
      "confidence": 0.55
    }
  ],
  "action": {
    "decision": "wait",
    "reason": "处于中阴阶段，新走势类型未确立，默认等待"
  },
  "risk": {
    "conflict_level": "low",
    "notes": "中阴阶段多空方向不明，需等待第三类买卖点确认"
  },
  "cn_summary": "前一走势已完成，当前处于中阴阶段。等待新走势类型确立（第三类买卖点出现）后再操作。"
}
```

## 7. 规则约束

- 每个信号必须给出 `trigger` 和 `invalid_if`。
- 禁止输出情绪化语句或收益承诺。
- 默认保守确认型：优先 `B2/B3` 与 `S2/S3`。
- 主级别优先，冲突高时优先 `wait` 或 `reduce`。
- 中阴阶段默认 `wait`，信号置信度上限 `0.6`。

## 8. 错误码建议

| 错误码 | 说明 |
|--------|------|
| `insufficient_data` | 关键数据缺失或样本不足最低要求 |
| `invalid_bars_format` | K 线字段不完整或类型错误 |
| `level_conflict_high` | 主次级别冲突高且无法安全决策 |
| `transitional_phase` | 处于中阴阶段，信号可靠性受限 |

## 9. 快速验收清单

- [ ] 字段是否齐全且枚举合法。
- [ ] `market_state` 包含 `trend_type`、`walk_type`、`phase`、`zhongshu_count`、`last_zhongshu`（含 `zd/zg/gg/dd`）、`current_stroke_dir`、`current_segment_dir`。
- [ ] `confidence` 是否在有效区间（`0.0-1.0`）。
- [ ] 中阴阶段信号置信度是否不超过 `0.6`。
- [ ] 是否包含触发与失效条件。
- [ ] 是否包含 `cn_summary`。
- [ ] 是否满足"仅分析不下单"。
- [ ] 数据不足时是否正确返回 `insufficient`。
