from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build simple Chan samples canvas")
    parser.add_argument("--data", required=True, help="Path to chan_fractal_cases.json")
    parser.add_argument("--max-cases", type=int, default=8)
    parser.add_argument(
        "--output",
        default="/root/.cursor/projects/root-crypto-quant-lab/canvases/chan-samples-overview.canvas.tsx",
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

type Bi = {
  direction: "up" | "down";
  start_index: number;
  end_index: number;
  start_price: number;
  end_price: number;
};

type Zhongshu = {
  zd: number;
  zg: number;
  gg: number;
  dd: number;
  start_index: number;
  end_index: number;
  evolution: string;
};

type Sample = {
  name: string;
  start_time: string;
  end_time: string;
  raw_count: number;
  merged_count: number;
  bi_count: number;
  zhongshu_count: number;
  merged: Bar[];
  bis: Bi[];
  zhongshus: Zhongshu[];
};

const SAMPLES = RAW_SAMPLES as Sample[];

function shortTime(value: string): string {
  return value.slice(0, 10);
}

function formatPrice(value: number): string {
  return Math.abs(value) >= 1000 ? value.toFixed(0) : value.toFixed(2);
}

function domain(bars: Bar[]): { min: number; max: number } {
  const min = Math.min(...bars.map((bar) => bar.low));
  const max = Math.max(...bars.map((bar) => bar.high));
  const pad = Math.max((max - min) * 0.08, 1);
  return { min: min - pad, max: max + pad };
}

function SampleChart({ sample }: { sample: Sample }) {
  const theme = useHostTheme();
  const width = 1120;
  const height = 300;
  const pad = { left: 54, right: 18, top: 20, bottom: 34 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const d = domain(sample.merged);
  const positions = new Map(sample.merged.map((bar, pos) => [bar.index, pos]));
  const step = plotW / Math.max(sample.merged.length - 1, 1);
  const candleW = Math.max(2, Math.min(8, step * 0.55));
  const x = (index: number) => pad.left + (positions.get(index) ?? 0) * step;
  const y = (price: number) => pad.top + ((d.max - price) / (d.max - d.min)) * plotH;
  const grid = [0, 0.25, 0.5, 0.75, 1].map((ratio) => d.min + (d.max - d.min) * ratio);

  return (
    <svg
      width="100%"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`${sample.name}: merged K lines with Bi and Zhongshu`}
    >
      <rect x={0} y={0} width={width} height={height} fill={theme.bg.editor} />

      {grid.map((value) => {
        const yy = y(value);
        return (
          <g key={`grid-${value}`}>
            <line x1={pad.left} x2={width - pad.right} y1={yy} y2={yy} stroke={theme.stroke.tertiary} />
            <text x={pad.left - 8} y={yy + 4} textAnchor="end" fill={theme.text.tertiary} fontSize={10}>
              {formatPrice(value)}
            </text>
          </g>
        );
      })}

      {sample.zhongshus.map((zs, index) => {
        if (!positions.has(zs.start_index) || !positions.has(zs.end_index)) return null;
        const x1 = x(zs.start_index);
        const x2 = x(zs.end_index);
        const innerTop = y(zs.zg);
        const innerBottom = y(zs.zd);
        const outerTop = y(zs.gg);
        const outerBottom = y(zs.dd);
        return (
          <g key={`zs-${index}`}>
            <rect
              x={Math.min(x1, x2)}
              y={Math.min(outerTop, outerBottom)}
              width={Math.max(Math.abs(x2 - x1), candleW)}
              height={Math.max(Math.abs(outerBottom - outerTop), 1)}
              fill={theme.fill.quaternary}
              stroke={theme.stroke.secondary}
              strokeDasharray="5 4"
            />
            <rect
              x={Math.min(x1, x2)}
              y={Math.min(innerTop, innerBottom)}
              width={Math.max(Math.abs(x2 - x1), candleW)}
              height={Math.max(Math.abs(innerBottom - innerTop), 2)}
              fill={theme.category.yellow}
              opacity={0.16}
            />
            <text x={Math.min(x1, x2) + 6} y={Math.min(innerTop, innerBottom) - 5} fill={theme.category.yellow} fontSize={10}>
              中枢 {formatPrice(zs.zd)}-{formatPrice(zs.zg)}
            </text>
          </g>
        );
      })}

      {sample.merged.map((bar) => {
        const xx = x(bar.index);
        const up = bar.close >= bar.open;
        const color = up ? theme.category.green : theme.category.pink;
        const top = y(Math.max(bar.open, bar.close));
        const bottom = y(Math.min(bar.open, bar.close));
        return (
          <g key={`bar-${bar.index}`}>
            <line x1={xx} x2={xx} y1={y(bar.high)} y2={y(bar.low)} stroke={color} strokeWidth={1} opacity={0.75} />
            <rect x={xx - candleW / 2} y={top} width={candleW} height={Math.max(bottom - top, 1)} fill={color} opacity={0.62} />
          </g>
        );
      })}

      {sample.bis.map((bi, index) => {
        if (!positions.has(bi.start_index) || !positions.has(bi.end_index)) return null;
        const color = bi.direction === "up" ? theme.category.green : theme.category.pink;
        return (
          <g key={`bi-${index}`}>
            <line
              x1={x(bi.start_index)}
              y1={y(bi.start_price)}
              x2={x(bi.end_index)}
              y2={y(bi.end_price)}
              stroke={color}
              strokeWidth={2}
              opacity={0.92}
            />
            <circle cx={x(bi.start_index)} cy={y(bi.start_price)} r={3} fill={color} />
            <circle cx={x(bi.end_index)} cy={y(bi.end_price)} r={3} fill={color} />
          </g>
        );
      })}

      <line x1={pad.left} x2={width - pad.right} y1={height - pad.bottom} y2={height - pad.bottom} stroke={theme.stroke.secondary} />
      <text x={pad.left + plotW / 2} y={height - 10} textAnchor="middle" fill={theme.text.secondary} fontSize={11}>
        merged K-line index
      </text>
      <text x={16} y={pad.top + plotH / 2} textAnchor="middle" fill={theme.text.secondary} fontSize={11} transform={`rotate(-90 16 ${pad.top + plotH / 2})`}>
        price
      </text>
    </svg>
  );
}

function SampleCard({ sample }: { sample: Sample }) {
  return (
    <Card size="lg">
      <CardHeader trailing={`${sample.bi_count} 笔 · ${sample.zhongshu_count} 中枢`}>
        {sample.name}
      </CardHeader>
      <CardBody>
        <Stack gap={8}>
          <SampleChart sample={sample} />
          <Grid columns={4} gap={10}>
            <Stat value={`${sample.raw_count}→${sample.merged_count}`} label="原始K→合并K" />
            <Stat value={sample.bi_count} label="笔" />
            <Stat value={sample.zhongshu_count} label="中枢" tone="warning" />
            <Stat value={`${shortTime(sample.start_time)} ~ ${shortTime(sample.end_time)}`} label="时间范围" />
          </Grid>
          <Text size="small" tone="tertiary">
            说明：K线为包含处理后的K线；绿色/粉色折线为笔；黄色实心区间为中枢 [ZD, ZG]，虚线框为 [DD, GG]。
          </Text>
        </Stack>
      </CardBody>
    </Card>
  );
}

export default function ChanSamplesOverviewCanvas() {
  return (
    <Stack gap={16} style={{ padding: 18, maxWidth: 1320, margin: "0 auto" }}>
      <Stack gap={5}>
        <H1>缠论走势样本：笔与中枢</H1>
        <Text tone="secondary">
          这里先隐藏细节表，只展示多段走势上的笔和中枢，方便快速人工扫图。
        </Text>
      </Stack>
      {SAMPLES.map((sample) => (
        <SampleCard key={sample.name} sample={sample} />
      ))}
    </Stack>
  );
}
'''


def _sample_payload(case: dict) -> dict:
    return {
        "name": case["name"],
        "start_time": case["start_time"],
        "end_time": case["end_time"],
        "raw_count": case["raw_count"],
        "merged_count": case["merged_count"],
        "bi_count": case["bi_count"],
        "zhongshu_count": case["zhongshu_count"],
        "merged": case["merged"],
        "bis": [
            {
                "direction": bi["direction"],
                "start_index": bi["start_index"],
                "end_index": bi["end_index"],
                "start_price": bi["start_price"],
                "end_price": bi["end_price"],
            }
            for bi in case["bis"]
        ],
        "zhongshus": [
            {
                "zd": zs["zd"],
                "zg": zs["zg"],
                "gg": zs["gg"],
                "dd": zs["dd"],
                "start_index": zs["start_index"],
                "end_index": zs["end_index"],
                "evolution": zs["evolution"],
            }
            for zs in case["zhongshus"]
        ],
    }


def main() -> None:
    args = parse_args()
    data = json.loads(Path(args.data).read_text(encoding="utf-8"))
    cases = [
        case
        for case in data["cases"]
        if case.get("source") == "binance" and case.get("bi_count", 0) > 0 and case.get("zhongshu_count", 0) > 0
    ]
    cases.sort(key=lambda case: (case["zhongshu_count"], case["bi_count"]), reverse=True)
    samples = [_sample_payload(case) for case in cases[: args.max_cases]]
    samples.sort(key=lambda case: case["start_time"])

    canvas = (
        'import {\n'
        '  Card,\n'
        '  CardBody,\n'
        '  CardHeader,\n'
        '  Grid,\n'
        '  H1,\n'
        '  Stack,\n'
        '  Stat,\n'
        '  Text,\n'
        '  useHostTheme,\n'
        '} from "cursor/canvas";\n\n'
        f"const RAW_SAMPLES = {json.dumps(samples, ensure_ascii=False, separators=(',', ':'))} as const;\n"
        f"{CANVAS_SUFFIX}\n"
    )
    Path(args.output).write_text(canvas, encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
