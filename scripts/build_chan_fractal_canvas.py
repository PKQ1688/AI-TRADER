from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Chan fractal review canvas")
    parser.add_argument("--data", required=True, help="Path to chan_fractal_cases.json")
    parser.add_argument(
        "--output",
        default="/root/.cursor/projects/root-crypto-quant-lab/canvases/chan-fractal-check.canvas.tsx",
    )
    return parser.parse_args()


CANVAS_SUFFIX = r'''
type Bar = {
  index: number;
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
};

type Trace = {
  merged_index: number;
  raw_indices: number[];
  direction: "up" | "down" | "unknown";
  raw_count: number;
};

type Fractal = {
  kind: "top" | "bottom";
  index: number;
  price: number;
  event_time: string;
  available_time: string;
  raw_indices: number[];
  check: "pass" | "fail";
  left_high: number;
  mid_high: number;
  right_high: number;
  left_low: number;
  mid_low: number;
  right_low: number;
};

type Bi = {
  direction: "up" | "down";
  start_index: number;
  end_index: number;
  start_kind: "top" | "bottom" | "unknown";
  end_kind: "top" | "bottom" | "unknown";
  start_price: number;
  end_price: number;
  bars_count: number;
  raw_indices: number[];
  event_time: string;
  available_time: string;
  check: "pass" | "fail";
  direction_ok: boolean;
  min_bars_ok: boolean;
  alternate_ok: boolean;
  endpoint_ok: boolean;
};

type Zhongshu = {
  zd: number;
  zg: number;
  gg: number;
  dd: number;
  g: number;
  d: number;
  start_index: number;
  end_index: number;
  event_time: string;
  available_time: string;
  evolution: "newborn" | "extension" | "expansion";
  status: string;
  source_bi_indices: number[];
  bi_count: number;
  check: "pass" | "fail";
};

type ChanCase = {
  name: string;
  source: string;
  raw_count: number;
  merged_count: number;
  merged_groups: number;
  fractal_count: number;
  top_count: number;
  bottom_count: number;
  bi_count: number;
  up_bi_count: number;
  down_bi_count: number;
  zhongshu_count: number;
  start_time: string;
  end_time: string;
  raw: Bar[];
  merged: Bar[];
  traces: Trace[];
  fractals: Fractal[];
  bis: Bi[];
  zhongshus: Zhongshu[];
};

type Dataset = {
  generated_at: string;
  args: Record<string, string | number>;
  rule_summary: string[];
  cases: ChanCase[];
};

const DATA = RAW_DATA as Dataset;

function shortTime(value: string): string {
  return value.replace("T", " ").replace(":00Z", "Z");
}

function formatPrice(value: number): string {
  if (Math.abs(value) >= 1000) {
    return value.toFixed(0);
  }
  return value.toFixed(2);
}

function rawRange(item: { raw_indices: number[] }): string {
  const indices = item.raw_indices;
  if (indices.length === 1) {
    return `${indices[0]}`;
  }
  return `${indices[0]}-${indices[indices.length - 1]}`;
}

function fractalLabel(fx: Fractal): string {
  return `${fx.kind === "top" ? "T" : "B"}${fx.index} · raw ${rawRange(fx)} · ${formatPrice(fx.price)}`;
}

function fractalKey(fx: Fractal): string {
  return `${fx.kind}-${fx.index}-${fx.available_time}`;
}

function biKey(bi: Bi): string {
  return `${bi.direction}-${bi.start_index}-${bi.end_index}-${bi.available_time}`;
}

function biLabel(bi: Bi): string {
  const dir = bi.direction === "up" ? "向上笔" : "向下笔";
  return `${dir} · ${bi.start_index}→${bi.end_index} · ${formatPrice(bi.start_price)}→${formatPrice(bi.end_price)}`;
}

function zhongshuKey(zs: Zhongshu): string {
  return `${zs.start_index}-${zs.end_index}-${zs.available_time}`;
}

function zhongshuLabel(zs: Zhongshu): string {
  return `K${zs.start_index}→K${zs.end_index} · ZD/ZG ${formatPrice(zs.zd)}-${formatPrice(zs.zg)} · ${zs.evolution}`;
}

function windowAround<T extends { index: number }>(items: T[], center: number, radius: number): T[] {
  const start = Math.max(0, center - radius);
  const end = center + radius;
  return items.filter((item) => item.index >= start && item.index <= end);
}

function chartDomain(bars: Bar[]): { min: number; max: number } {
  const highs = bars.map((bar) => bar.high);
  const lows = bars.map((bar) => bar.low);
  const min = Math.min(...lows);
  const max = Math.max(...highs);
  const padding = Math.max((max - min) * 0.08, 1);
  return { min: min - padding, max: max + padding };
}

function CandleChart({
  title,
  bars,
  source,
  timeRange,
  traces,
  fractals,
  bis,
  zhongshus,
  mode,
  selectedFractal,
  selectedBi,
  selectedZhongshu,
  height = 300,
}: {
  title: string;
  bars: Bar[];
  source: string;
  timeRange: string;
  traces?: Trace[];
  fractals?: Fractal[];
  bis?: Bi[];
  zhongshus?: Zhongshu[];
  mode: "raw" | "merged";
  selectedFractal?: Fractal;
  selectedBi?: Bi;
  selectedZhongshu?: Zhongshu;
  height?: number;
}) {
  const theme = useHostTheme();
  const width = 980;
  const pad = { left: 58, right: 20, top: 22, bottom: 46 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const domain = chartDomain(bars);
  const denom = Math.max(bars.length - 1, 1);
  const xStep = plotWidth / denom;
  const positions = new Map(bars.map((bar, position) => [bar.index, position]));
  const x = (index: number) => pad.left + (positions.get(index) ?? 0) * xStep;
  const y = (price: number) =>
    pad.top + ((domain.max - price) / (domain.max - domain.min)) * plotHeight;
  const candleWidth = Math.max(4, Math.min(22, xStep * 0.58));
  const gridValues = [0, 0.25, 0.5, 0.75, 1].map(
    (ratio) => domain.min + (domain.max - domain.min) * ratio
  );

  return (
    <Stack gap={6}>
      <Text weight="semibold">{title}</Text>
      <svg
        width="100%"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`${title}. X axis is K-line index. Y axis is price.`}
      >
        <rect
          x={0}
          y={0}
          width={width}
          height={height}
          fill={theme.bg.editor}
        />
        {gridValues.map((value) => {
          const yy = y(value);
          return (
            <g key={`grid-${value}`}>
              <line
                x1={pad.left}
                x2={width - pad.right}
                y1={yy}
                y2={yy}
                stroke={theme.stroke.tertiary}
              />
              <text
                x={pad.left - 8}
                y={yy + 4}
                textAnchor="end"
                fill={theme.text.tertiary}
                fontSize={11}
              >
                {formatPrice(value)}
              </text>
            </g>
          );
        })}
        {mode === "raw" &&
          traces
            ?.filter((trace) => trace.raw_count > 1 && trace.raw_indices.every((idx) => positions.has(idx)))
            .map((trace) => {
              const start = x(trace.raw_indices[0]) - xStep * 0.48;
              const end = x(trace.raw_indices[trace.raw_indices.length - 1]) + xStep * 0.48;
              return (
                <g key={`trace-${trace.merged_index}`}>
                  <rect
                    x={start}
                    y={pad.top}
                    width={Math.max(end - start, candleWidth)}
                    height={plotHeight}
                    fill={theme.fill.tertiary}
                  />
                  <text
                    x={(start + end) / 2}
                    y={pad.top + 14}
                    textAnchor="middle"
                    fill={theme.category.yellow}
                    fontSize={10}
                  >
                    M{trace.merged_index} {trace.direction}
                  </text>
                </g>
              );
            })}
        {mode === "merged" &&
          zhongshus
            ?.filter((zs) => positions.has(zs.start_index) && positions.has(zs.end_index))
            .map((zs) => {
              const startX = x(zs.start_index);
              const endX = x(zs.end_index);
              const topY = y(zs.zg);
              const bottomY = y(zs.zd);
              const waveTopY = y(zs.gg);
              const waveBottomY = y(zs.dd);
              const isSelected =
                selectedZhongshu && zhongshuKey(selectedZhongshu) === zhongshuKey(zs);
              return (
                <g key={`zs-${zhongshuKey(zs)}`}>
                  <rect
                    x={Math.min(startX, endX)}
                    y={Math.min(waveTopY, waveBottomY)}
                    width={Math.max(Math.abs(endX - startX), candleWidth)}
                    height={Math.max(Math.abs(waveBottomY - waveTopY), 1)}
                    fill={theme.fill.quaternary}
                    stroke={isSelected ? theme.accent.primary : theme.stroke.secondary}
                    strokeDasharray="5 4"
                    strokeWidth={isSelected ? 1.6 : 1}
                  />
                  <rect
                    x={Math.min(startX, endX)}
                    y={Math.min(topY, bottomY)}
                    width={Math.max(Math.abs(endX - startX), candleWidth)}
                    height={Math.max(Math.abs(bottomY - topY), 2)}
                    fill={theme.category.yellow}
                    opacity={isSelected ? 0.24 : 0.14}
                  />
                  <text
                    x={Math.min(startX, endX) + 6}
                    y={Math.min(topY, bottomY) - 6}
                    fill={isSelected ? theme.accent.primary : theme.category.yellow}
                    fontSize={11}
                    fontWeight={isSelected ? 700 : 500}
                  >
                    ZS {formatPrice(zs.zd)}-{formatPrice(zs.zg)}
                  </text>
                </g>
              );
            })}
        {bars.map((bar) => {
          const xx = x(bar.index);
          const up = bar.close >= bar.open;
          const color = up ? theme.category.green : theme.category.pink;
          const bodyTop = y(Math.max(bar.open, bar.close));
          const bodyBottom = y(Math.min(bar.open, bar.close));
          return (
            <g key={`bar-${bar.index}`}>
              <line
                x1={xx}
                x2={xx}
                y1={y(bar.high)}
                y2={y(bar.low)}
                stroke={color}
                strokeWidth={1.2}
              />
              <rect
                x={xx - candleWidth / 2}
                y={bodyTop}
                width={candleWidth}
                height={Math.max(bodyBottom - bodyTop, 1.5)}
                fill={color}
                opacity={0.72}
              />
              <text
                x={xx}
                y={height - pad.bottom + 16}
                textAnchor="middle"
                fill={theme.text.quaternary}
                fontSize={10}
              >
                {bar.index}
              </text>
            </g>
          );
        })}
        {mode === "merged" &&
          bis
            ?.filter((bi) => positions.has(bi.start_index) && positions.has(bi.end_index))
            .map((bi) => {
              const startX = x(bi.start_index);
              const endX = x(bi.end_index);
              const startY = y(bi.start_price);
              const endY = y(bi.end_price);
              const isSelected = selectedBi && biKey(selectedBi) === biKey(bi);
              const color = bi.direction === "up" ? theme.category.green : theme.category.pink;
              return (
                <g key={`bi-${biKey(bi)}`}>
                  <line
                    x1={startX}
                    y1={startY}
                    x2={endX}
                    y2={endY}
                    stroke={isSelected ? theme.accent.primary : color}
                    strokeWidth={isSelected ? 3 : 1.8}
                    opacity={isSelected ? 1 : 0.72}
                  />
                  <circle cx={startX} cy={startY} r={isSelected ? 5 : 3} fill={color} />
                  <circle cx={endX} cy={endY} r={isSelected ? 5 : 3} fill={color} />
                </g>
              );
            })}
        {mode === "merged" &&
          fractals?.filter((fx) => positions.has(fx.index)).map((fx) => {
            const xx = x(fx.index);
            const yy = y(fx.price);
            const isTop = fx.kind === "top";
            const color = isTop ? theme.category.orange : theme.category.blue;
            const labelY = isTop ? yy - 12 : yy + 20;
            const isSelected = selectedFractal && fractalKey(selectedFractal) === fractalKey(fx);
            return (
              <g key={`fx-${fx.kind}-${fx.index}`}>
                {isSelected && (
                  <line
                    x1={xx}
                    x2={xx}
                    y1={pad.top}
                    y2={height - pad.bottom}
                    stroke={theme.accent.primary}
                    strokeWidth={1.4}
                    strokeDasharray="4 4"
                  />
                )}
                <circle cx={xx} cy={yy} r={isSelected ? 7 : 5} fill={color} />
                <text
                  x={xx}
                  y={labelY}
                  textAnchor="middle"
                  fill={color}
                  fontSize={isSelected ? 13 : 11}
                  fontWeight={600}
                >
                  {isTop ? "T" : "B"}{fx.index}
                </text>
              </g>
            );
          })}
        <line
          x1={pad.left}
          x2={width - pad.right}
          y1={height - pad.bottom}
          y2={height - pad.bottom}
          stroke={theme.stroke.secondary}
        />
        <line
          x1={pad.left}
          x2={pad.left}
          y1={pad.top}
          y2={height - pad.bottom}
          stroke={theme.stroke.secondary}
        />
        <text
          x={pad.left + plotWidth / 2}
          y={height - 12}
          textAnchor="middle"
          fill={theme.text.secondary}
          fontSize={12}
        >
          K-line index
        </text>
        <text
          x={16}
          y={pad.top + plotHeight / 2}
          textAnchor="middle"
          fill={theme.text.secondary}
          fontSize={12}
          transform={`rotate(-90 16 ${pad.top + plotHeight / 2})`}
        >
          Price
        </text>
      </svg>
      <Text size="small" tone="tertiary">
        Source: {source} · Time range: {timeRange} · X axis: K-line index · Y axis: price
      </Text>
    </Stack>
  );
}

function FractalTable({ item }: { item: ChanCase }) {
  return (
    <Table
      headers={[
        "类型",
        "合并K索引",
        "原始K范围",
        "价格",
        "High L/M/R",
        "Low L/M/R",
        "确认时间",
        "校验",
      ]}
      rows={item.fractals.map((fx) => [
        fx.kind === "top" ? "顶分型" : "底分型",
        fx.index,
        rawRange(fx),
        formatPrice(fx.price),
        `${formatPrice(fx.left_high)} / ${formatPrice(fx.mid_high)} / ${formatPrice(fx.right_high)}`,
        `${formatPrice(fx.left_low)} / ${formatPrice(fx.mid_low)} / ${formatPrice(fx.right_low)}`,
        shortTime(fx.available_time),
        fx.check,
      ])}
      rowTone={item.fractals.map((fx) => (fx.check === "pass" ? "success" : "danger"))}
      columnAlign={["left", "right", "left", "right", "left", "left", "left", "left"]}
      stickyHeader
      style={{ maxHeight: 280 }}
    />
  );
}

function BiTable({ item }: { item: ChanCase }) {
  return (
    <Table
      headers={[
        "方向",
        "起止合并K",
        "起点",
        "终点",
        "合并K根数",
        "原始K范围",
        "确认时间",
        "校验",
      ]}
      rows={item.bis.map((bi) => [
        bi.direction === "up" ? "向上" : "向下",
        `${bi.start_index} → ${bi.end_index}`,
        `${bi.start_kind} ${formatPrice(bi.start_price)}`,
        `${bi.end_kind} ${formatPrice(bi.end_price)}`,
        bi.bars_count,
        rawRange(bi),
        shortTime(bi.available_time),
        bi.check,
      ])}
      rowTone={item.bis.map((bi) => (bi.check === "pass" ? "success" : "danger"))}
      columnAlign={["left", "left", "left", "left", "right", "left", "left", "left"]}
      stickyHeader
      style={{ maxHeight: 260 }}
    />
  );
}

function ZhongshuTable({ item }: { item: ChanCase }) {
  return (
    <Table
      headers={[
        "范围",
        "ZD/ZG",
        "DD/GG",
        "来源笔",
        "演化",
        "确认时间",
        "校验",
      ]}
      rows={item.zhongshus.map((zs) => [
        `K${zs.start_index} → K${zs.end_index}`,
        `${formatPrice(zs.zd)} / ${formatPrice(zs.zg)}`,
        `${formatPrice(zs.dd)} / ${formatPrice(zs.gg)}`,
        zs.source_bi_indices.join(","),
        zs.evolution,
        shortTime(zs.available_time),
        zs.check,
      ])}
      rowTone={item.zhongshus.map((zs) => (zs.check === "pass" ? "success" : "danger"))}
      columnAlign={["left", "left", "left", "left", "left", "left", "left"]}
      stickyHeader
      style={{ maxHeight: 240 }}
    />
  );
}

function MergeTable({ item }: { item: ChanCase }) {
  const rows = item.traces
    .filter((trace) => trace.raw_count > 1)
    .map((trace) => {
      const bar = item.merged[trace.merged_index];
      return [
        trace.merged_index,
        rawRange(trace),
        trace.direction,
        trace.raw_count,
        `${formatPrice(bar.high)} / ${formatPrice(bar.low)}`,
        shortTime(bar.time),
      ];
    });

  return (
    <Table
      headers={["合并K索引", "原始K范围", "方向", "合并根数", "High / Low", "合并后时间"]}
      rows={rows}
      columnAlign={["right", "left", "left", "right", "left", "left"]}
      rowTone={rows.map(() => "info")}
      stickyHeader
      style={{ maxHeight: 220 }}
    />
  );
}

function CurrentZhongshuPanel({
  item,
  selectedKey,
  setSelectedKey,
  zs,
}: {
  item: ChanCase;
  selectedKey: string;
  setSelectedKey: (value: string) => void;
  zs: Zhongshu;
}) {
  return (
    <Stack gap={14}>
      <Stack gap={6}>
        <H2>当前中枢</H2>
        <Select
          value={selectedKey}
          onChange={setSelectedKey}
          options={item.zhongshus.map((candidate) => ({
            value: zhongshuKey(candidate),
            label: zhongshuLabel(candidate),
          }))}
          style={{ width: "100%" }}
        />
      </Stack>

      <Grid columns={2} gap={10}>
        <Stat value={`${formatPrice(zs.zd)} - ${formatPrice(zs.zg)}`} label="中枢区间 ZD/ZG" />
        <Stat value={`${formatPrice(zs.dd)} - ${formatPrice(zs.gg)}`} label="波动区间 DD/GG" />
        <Stat value={zs.bi_count} label="来源笔数量" tone={zs.bi_count >= 3 ? "success" : "danger"} />
        <Stat value={zs.evolution} label="演化状态" tone={zs.evolution === "newborn" ? "info" : "warning"} />
      </Grid>

      <Grid columns={2} gap={8}>
        <Callout tone={zs.zd <= zs.zg ? "success" : "danger"} title="区间有效性">
          {`ZD <= ZG · ${formatPrice(zs.zd)} <= ${formatPrice(zs.zg)}`}
        </Callout>
        <Callout tone={zs.bi_count >= 3 ? "success" : "danger"} title="至少三笔重叠">
          来源笔：{zs.source_bi_indices.join(", ")}
        </Callout>
      </Grid>
    </Stack>
  );
}

function BiRuleChecks({ bi }: { bi: Bi }) {
  return (
    <Grid columns={2} gap={8}>
      <Callout tone={bi.alternate_ok ? "success" : "danger"} title="顶底交替">
        {bi.start_kind} → {bi.end_kind}
      </Callout>
      <Callout tone={bi.direction_ok ? "success" : "danger"} title="方向与价格">
        {bi.direction === "up" ? "底到顶，终点高于起点" : "顶到底，终点低于起点"}
      </Callout>
      <Callout tone={bi.min_bars_ok ? "success" : "danger"} title="合并K根数">
        {bi.bars_count} 根合并 K，要求至少 {DATA.args.min_stroke_bars} 根
      </Callout>
      <Callout tone={bi.endpoint_ok ? "success" : "danger"} title="端点落在分型K">
        起点 {formatPrice(bi.start_price)}，终点 {formatPrice(bi.end_price)}
      </Callout>
    </Grid>
  );
}

function CurrentBiPanel({
  item,
  selectedKey,
  setSelectedKey,
  bi,
}: {
  item: ChanCase;
  selectedKey: string;
  setSelectedKey: (value: string) => void;
  bi: Bi;
}) {
  return (
    <Stack gap={14}>
      <Stack gap={6}>
        <H2>当前笔</H2>
        <Select
          value={selectedKey}
          onChange={setSelectedKey}
          options={item.bis.map((candidate) => ({
            value: biKey(candidate),
            label: biLabel(candidate),
          }))}
          style={{ width: "100%" }}
        />
      </Stack>

      <Grid columns={2} gap={10}>
        <Stat value={bi.direction === "up" ? "向上" : "向下"} label="方向" tone={bi.direction === "up" ? "success" : "danger"} />
        <Stat value={`${bi.start_index} → ${bi.end_index}`} label="合并K索引" />
        <Stat value={bi.bars_count} label="合并K根数" tone={bi.min_bars_ok ? "success" : "danger"} />
        <Stat value={bi.check} label="自动校验" tone={bi.check === "pass" ? "success" : "danger"} />
      </Grid>

      <Grid columns={2} gap={8}>
        <Card variant="borderless">
          <CardBody style={{ padding: 10 }}>
            <Text size="small" tone="tertiary">起点分型</Text>
            <Text weight="semibold">{bi.start_kind} · K{bi.start_index}</Text>
            <Text>{formatPrice(bi.start_price)}</Text>
          </CardBody>
        </Card>
        <Card variant="borderless">
          <CardBody style={{ padding: 10 }}>
            <Text size="small" tone="tertiary">终点分型</Text>
            <Text weight="semibold">{bi.end_kind} · K{bi.end_index}</Text>
            <Text>{formatPrice(bi.end_price)}</Text>
          </CardBody>
        </Card>
      </Grid>

      <BiRuleChecks bi={bi} />
    </Stack>
  );
}

function ComparisonStrip({ fx }: { fx: Fractal }) {
  const theme = useHostTheme();
  const highPass =
    fx.kind === "top"
      ? fx.mid_high > fx.left_high && fx.mid_high > fx.right_high
      : fx.mid_high < fx.left_high && fx.mid_high < fx.right_high;
  const lowPass =
    fx.kind === "top"
      ? fx.mid_low > fx.left_low && fx.mid_low > fx.right_low
      : fx.mid_low < fx.left_low && fx.mid_low < fx.right_low;
  const relation = fx.kind === "top" ? "mid > left/right" : "mid < left/right";
  const cell = (label: string, high: number, low: number, active: boolean) => (
    <div
      style={{
        border: `1px solid ${active ? theme.accent.primary : theme.stroke.secondary}`,
        background: active ? theme.fill.secondary : theme.fill.quaternary,
        borderRadius: 6,
        padding: 10,
        minWidth: 0,
      }}
    >
      <Text size="small" tone="tertiary">{label}</Text>
      <Text weight="semibold">H {formatPrice(high)}</Text>
      <Text weight="semibold">L {formatPrice(low)}</Text>
    </div>
  );

  return (
    <Stack gap={8}>
      <Grid columns={3} gap={8}>
        {cell("left", fx.left_high, fx.left_low, false)}
        {cell("mid", fx.mid_high, fx.mid_low, true)}
        {cell("right", fx.right_high, fx.right_low, false)}
      </Grid>
      <Grid columns={2} gap={8}>
        <Callout tone={highPass ? "success" : "danger"} title="High 判定">
          {relation} · {formatPrice(fx.left_high)} / {formatPrice(fx.mid_high)} / {formatPrice(fx.right_high)}
        </Callout>
        <Callout tone={lowPass ? "success" : "danger"} title="Low 判定">
          {relation} · {formatPrice(fx.left_low)} / {formatPrice(fx.mid_low)} / {formatPrice(fx.right_low)}
        </Callout>
      </Grid>
    </Stack>
  );
}

function FocusedBiReview({ item, bi }: { item: ChanCase; bi: Bi }) {
  const localMerged = item.merged.filter(
    (bar) => bar.index >= Math.max(0, bi.start_index - 3) && bar.index <= bi.end_index + 3
  );
  const localFractals = item.fractals.filter((fx) =>
    localMerged.some((bar) => bar.index === fx.index)
  );
  const localBis = item.bis.filter(
    (candidate) => candidate.end_index >= localMerged[0].index && candidate.start_index <= localMerged[localMerged.length - 1].index
  );
  const rawStart = Math.max(0, bi.raw_indices[0] - 5);
  const rawEnd = Math.min(item.raw.length - 1, bi.raw_indices[bi.raw_indices.length - 1] + 5);
  const localRaw = item.raw.filter((bar) => bar.index >= rawStart && bar.index <= rawEnd);
  const localTraces = item.traces.filter((trace) =>
    trace.raw_indices.some((idx) => idx >= rawStart && idx <= rawEnd)
  );
  const timeRange = `${shortTime(localMerged[0].time)} ~ ${shortTime(localMerged[localMerged.length - 1].time)}`;

  return (
    <Stack gap={14}>
      <Card size="lg">
        <CardHeader trailing={<Pill active>{bi.direction === "up" ? "向上笔" : "向下笔"}</Pill>}>
          当前笔放大检查
        </CardHeader>
        <CardBody>
          <Stack gap={12}>
            <Row gap={10} align="center" wrap>
              <Text weight="semibold">{biLabel(bi)}</Text>
              <Text tone="tertiary" size="small">
                raw {rawRange(bi)} · available: {shortTime(bi.available_time)}
              </Text>
            </Row>
            <CandleChart
              title={`Focused merged K-lines and Bi · ${biLabel(bi)}`}
              bars={localMerged}
              fractals={localFractals}
              bis={localBis}
              selectedBi={bi}
              source={`${item.source} ${DATA.args.symbol ?? ""} ${DATA.args.timeframe ?? ""}`}
              timeRange={timeRange}
              mode="merged"
              height={410}
            />
          </Stack>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>该笔覆盖的原始 K 线局部</CardHeader>
        <CardBody>
          <CandleChart
            title={`Raw K-lines covered by selected Bi`}
            bars={localRaw}
            traces={localTraces}
            source={`${item.source} ${DATA.args.symbol ?? ""} ${DATA.args.timeframe ?? ""}`}
            timeRange={`${shortTime(localRaw[0].time)} ~ ${shortTime(localRaw[localRaw.length - 1].time)}`}
            mode="raw"
            height={230}
          />
        </CardBody>
      </Card>
    </Stack>
  );
}

function FocusedFractalReview({ item, fx }: { item: ChanCase; fx: Fractal }) {
  const trace = item.traces[fx.index];
  const localMerged = windowAround(item.merged, fx.index, 4);
  const localFractals = item.fractals.filter((candidate) =>
    localMerged.some((bar) => bar.index === candidate.index)
  );
  const rawStart = Math.max(0, trace.raw_indices[0] - 5);
  const rawEnd = Math.min(item.raw.length - 1, trace.raw_indices[trace.raw_indices.length - 1] + 5);
  const localRaw = item.raw.filter((bar) => bar.index >= rawStart && bar.index <= rawEnd);
  const localTraces = item.traces.filter((candidate) =>
    candidate.raw_indices.some((idx) => idx >= rawStart && idx <= rawEnd)
  );
  const timeRange = `${shortTime(localMerged[0].time)} ~ ${shortTime(localMerged[localMerged.length - 1].time)}`;

  return (
    <Stack gap={14}>
      <Card size="lg">
        <CardHeader trailing={<Pill active>{fx.kind === "top" ? "顶分型" : "底分型"}</Pill>}>
          当前分型放大检查
        </CardHeader>
        <CardBody>
          <Stack gap={12}>
            <Row gap={10} align="center" wrap>
              <Text weight="semibold">{fractalLabel(fx)}</Text>
              <Text tone="tertiary" size="small">
                event: {shortTime(fx.event_time)} · available: {shortTime(fx.available_time)}
              </Text>
            </Row>
            <CandleChart
              title={`Focused merged K-lines around ${fractalLabel(fx)}`}
              bars={localMerged}
              fractals={localFractals}
              selectedFractal={fx}
              source={`${item.source} ${DATA.args.symbol ?? ""} ${DATA.args.timeframe ?? ""}`}
              timeRange={timeRange}
              mode="merged"
              height={390}
            />
          </Stack>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>该分型对应的原始 K 线局部</CardHeader>
        <CardBody>
          <CandleChart
            title={`Raw K-lines near merged index ${fx.index}`}
            bars={localRaw}
            traces={localTraces}
            source={`${item.source} ${DATA.args.symbol ?? ""} ${DATA.args.timeframe ?? ""}`}
            timeRange={`${shortTime(localRaw[0].time)} ~ ${shortTime(localRaw[localRaw.length - 1].time)}`}
            mode="raw"
            height={230}
          />
        </CardBody>
      </Card>
    </Stack>
  );
}

function CurrentFractalPanel({
  item,
  selectedKey,
  setSelectedKey,
  fx,
}: {
  item: ChanCase;
  selectedKey: string;
  setSelectedKey: (value: string) => void;
  fx: Fractal;
}) {
  return (
    <Stack gap={14}>
      <Stack gap={6}>
        <H2>当前分型</H2>
        <Select
          value={selectedKey}
          onChange={setSelectedKey}
          options={item.fractals.map((candidate) => ({
            value: fractalKey(candidate),
            label: fractalLabel(candidate),
          }))}
          style={{ width: "100%" }}
        />
      </Stack>

      <Grid columns={2} gap={10}>
        <Stat value={fx.index} label="合并K索引" />
        <Stat value={`raw ${rawRange(fx)}`} label="原始K范围" />
        <Stat value={formatPrice(fx.price)} label="分型价格" tone={fx.kind === "top" ? "warning" : "info"} />
        <Stat value={fx.check} label="数值校验" tone={fx.check === "pass" ? "success" : "danger"} />
      </Grid>

      <ComparisonStrip fx={fx} />
    </Stack>
  );
}

function RuleSummary() {
  return (
    <Stack gap={6}>
      <H2>判定口径</H2>
      {DATA.rule_summary.map((rule, index) => (
        <Text key={index} tone="secondary">
          {index + 1}. {rule}
        </Text>
      ))}
    </Stack>
  );
}

export default function ChanFractalCheckCanvas() {
  const defaultCase =
    DATA.cases.reduce((best, candidate) =>
      candidate.bi_count > best.bi_count ? candidate : best
    , DATA.cases[0]);
  const defaultBi = defaultCase.bis[0];
  const defaultZhongshu = defaultCase.zhongshus[0];
  const [selectedName, setSelectedName] = useCanvasState("selected-case", defaultCase.name);
  const [selectedBiKey, setSelectedBiKey] = useCanvasState(
    "selected-bi",
    defaultBi ? biKey(defaultBi) : ""
  );
  const [selectedZhongshuKey, setSelectedZhongshuKey] = useCanvasState(
    "selected-zhongshu",
    defaultZhongshu ? zhongshuKey(defaultZhongshu) : ""
  );
  const item = DATA.cases.find((candidate) => candidate.name === selectedName) ?? DATA.cases[0];
  const selectedBi =
    item.bis.find((candidate) => biKey(candidate) === selectedBiKey) ??
    item.bis[0];
  const selectedZhongshu =
    item.zhongshus.find((candidate) => zhongshuKey(candidate) === selectedZhongshuKey) ??
    item.zhongshus[0];
  const selectedFractal =
    item.fractals.find((candidate) => candidate.index === selectedBi?.end_index) ??
    item.fractals[0];
  const checkFails = item.fractals.filter((fx) => fx.check !== "pass").length;
  const biCheckFails = item.bis.filter((bi) => bi.check !== "pass").length;
  const zhongshuCheckFails = item.zhongshus.filter((zs) => zs.check !== "pass").length;
  const timeRange = `${shortTime(item.start_time)} ~ ${shortTime(item.end_time)}`;

  return (
    <Stack gap={18} style={{ padding: 18, maxWidth: 1440, margin: "0 auto" }}>
      <Stack gap={6}>
        <H1>缠论 K 线包含与顶底分型人工核对</H1>
        <Text tone="secondary">
          中枢用黄色区间标在连续笔总览上：实心区域是 [ZD, ZG]，虚线外框是 [DD, GG] 波动区间。
        </Text>
      </Stack>

      <Row gap={12} align="center" wrap>
        <Select
          value={item.name}
          onChange={setSelectedName}
          options={DATA.cases.map((candidate) => ({
            value: candidate.name,
            label: `${candidate.name} · ${candidate.zhongshu_count} 中枢 · ${candidate.bi_count} 笔`,
          }))}
          style={{ minWidth: 360 }}
        />
        <Pill active>{item.source}</Pill>
        <Text tone="tertiary" size="small">
          数据生成时间：{shortTime(DATA.generated_at)}
        </Text>
      </Row>

      <Grid columns={6} gap={12}>
        <Stat value={`${item.raw_count} → ${item.merged_count}`} label="原始K → 合并K" />
        <Stat value={item.merged_groups} label="包含合并组数" tone="info" />
        <Stat value={item.fractal_count} label="分型总数" />
        <Stat value={item.bi_count} label="笔总数" />
        <Stat value={item.zhongshu_count} label="中枢总数" tone="warning" />
        <Stat value={`${item.up_bi_count}/${item.down_bi_count}`} label="上笔/下笔" />
      </Grid>

      <Callout tone={checkFails === 0 && biCheckFails === 0 && zhongshuCheckFails === 0 ? "success" : "danger"} title="自动 double check">
        {checkFails === 0 && biCheckFails === 0 && zhongshuCheckFails === 0
          ? "当前 case 中所有分型、笔和中枢均通过数值条件复核。"
          : `当前 case 中有 ${checkFails} 个分型、${biCheckFails} 笔、${zhongshuCheckFails} 个中枢未通过复核，请优先检查。`}
      </Callout>

      <Card size="lg">
        <CardHeader>连续笔总览</CardHeader>
        <CardBody>
          <CandleChart
            title={`Continuous Bi overview · ${item.name}`}
            bars={item.merged}
            fractals={item.fractals}
            bis={item.bis}
            zhongshus={item.zhongshus}
            selectedFractal={selectedFractal}
            selectedBi={selectedBi}
            selectedZhongshu={selectedZhongshu}
            source={`${item.source} ${DATA.args.symbol ?? ""} ${DATA.args.timeframe ?? ""}`}
            timeRange={timeRange}
            mode="merged"
            height={420}
          />
        </CardBody>
      </Card>

      <Grid columns="360px minmax(0, 1fr)" gap={16} align="start">
        <Card size="lg">
          <CardHeader>当前中枢选择与校验</CardHeader>
          <CardBody>
            {selectedZhongshu ? (
              <CurrentZhongshuPanel
                item={item}
                selectedKey={zhongshuKey(selectedZhongshu)}
                setSelectedKey={setSelectedZhongshuKey}
                zs={selectedZhongshu}
              />
            ) : (
              <Callout tone="warning" title="当前 case 未形成中枢">
                该 case 尚未形成三笔重叠中枢，请选择 BTCUSDT 的真实 case。
              </Callout>
            )}
          </CardBody>
        </Card>

        <Card size="lg">
          <CardHeader>中枢局部放大</CardHeader>
          <CardBody>
            {selectedZhongshu ? (
              <CandleChart
                title={`Focused Zhongshu · ${zhongshuLabel(selectedZhongshu)}`}
                bars={item.merged.filter(
                  (bar) =>
                    bar.index >= Math.max(0, selectedZhongshu.start_index - 4) &&
                    bar.index <= selectedZhongshu.end_index + 4
                )}
                fractals={item.fractals.filter(
                  (fx) =>
                    fx.index >= Math.max(0, selectedZhongshu.start_index - 4) &&
                    fx.index <= selectedZhongshu.end_index + 4
                )}
                bis={item.bis.filter(
                  (bi) =>
                    bi.end_index >= Math.max(0, selectedZhongshu.start_index - 4) &&
                    bi.start_index <= selectedZhongshu.end_index + 4
                )}
                zhongshus={[selectedZhongshu]}
                selectedZhongshu={selectedZhongshu}
                source={`${item.source} ${DATA.args.symbol ?? ""} ${DATA.args.timeframe ?? ""}`}
                timeRange={timeRange}
                mode="merged"
                height={360}
              />
            ) : (
              <Text tone="secondary">当前 case 没有中枢可放大。</Text>
            )}
          </CardBody>
        </Card>
      </Grid>

      <Grid columns="360px minmax(0, 1fr)" gap={16} align="start">
        <Card size="lg">
          <CardHeader>当前笔选择与校验</CardHeader>
          <CardBody>
            {selectedBi ? (
              <CurrentBiPanel
                item={item}
                selectedKey={biKey(selectedBi)}
                setSelectedKey={setSelectedBiKey}
                bi={selectedBi}
              />
            ) : (
              <Callout tone="warning" title="当前 case 未形成笔">
                该 case 有分型，但不满足当前成笔规则；请选择 BTCUSDT 的真实 case 继续检查。
              </Callout>
            )}
          </CardBody>
        </Card>

        {selectedBi ? (
          <FocusedBiReview item={item} bi={selectedBi} />
        ) : (
          <FocusedFractalReview item={item} fx={selectedFractal} />
        )}
      </Grid>

      <Grid columns="minmax(0, 1.15fr) minmax(340px, 0.85fr)" gap={16} align="start">
        <Stack gap={14}>
          <Card collapsible defaultOpen={false}>
            <CardHeader>原始 K 线与包含合并组</CardHeader>
            <CardBody>
              <CandleChart
                title={`Full raw K-lines with inclusion groups · ${item.name}`}
                bars={item.raw}
                traces={item.traces}
                source={`${item.source} ${DATA.args.symbol ?? ""} ${DATA.args.timeframe ?? ""}`}
                timeRange={timeRange}
                mode="raw"
                height={250}
              />
            </CardBody>
          </Card>

          <Card collapsible defaultOpen={false}>
            <CardHeader>合并后 K 线与顶底分型</CardHeader>
            <CardBody>
              <CandleChart
                title={`Full merged K-lines with top/bottom fractals · ${item.name}`}
                bars={item.merged}
                fractals={item.fractals}
                bis={item.bis}
                zhongshus={item.zhongshus}
                selectedFractal={selectedFractal}
                selectedBi={selectedBi}
                selectedZhongshu={selectedZhongshu}
                source={`${item.source} ${DATA.args.symbol ?? ""} ${DATA.args.timeframe ?? ""}`}
                timeRange={timeRange}
                mode="merged"
                height={260}
              />
            </CardBody>
          </Card>
        </Stack>

        <Stack gap={14}>
          <RuleSummary />
          <Divider />
          <Stack gap={8}>
            <H2>包含合并 trace</H2>
            <MergeTable item={item} />
          </Stack>
          <Stack gap={8}>
            <H2>中枢列表与复核</H2>
            <ZhongshuTable item={item} />
          </Stack>
          <Stack gap={8}>
            <H2>笔列表与复核</H2>
            <BiTable item={item} />
          </Stack>
          <Stack gap={8}>
            <H2>分型数值复核</H2>
            <FractalTable item={item} />
          </Stack>
        </Stack>
      </Grid>
    </Stack>
  );
}
'''


def main() -> None:
    args = parse_args()
    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    data_literal = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    canvas = (
        'import {\n'
        '  Callout,\n'
        '  Card,\n'
        '  CardBody,\n'
        '  CardHeader,\n'
        '  Divider,\n'
        '  Grid,\n'
        '  H1,\n'
        '  H2,\n'
        '  Pill,\n'
        '  Row,\n'
        '  Select,\n'
        '  Stack,\n'
        '  Stat,\n'
        '  Table,\n'
        '  Text,\n'
        '  useCanvasState,\n'
        '  useHostTheme,\n'
        '} from "cursor/canvas";\n\n'
        f"const RAW_DATA = {data_literal} as const;\n"
        f"{CANVAS_SUFFIX}\n"
    )
    Path(args.output).write_text(canvas, encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
