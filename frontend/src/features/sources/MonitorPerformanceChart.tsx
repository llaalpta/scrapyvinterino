import { useMemo, useState } from 'react';
import type { MouseEvent } from 'react';
import { Bar, BarChart, CartesianGrid, ReferenceArea, ReferenceLine, ResponsiveContainer, XAxis, YAxis } from 'recharts';
import type { MonitorStatsRange } from '../../api';

type MonitorChartPoint = {
  bucketEndMs: number;
  bucketStartMs: number;
  itemsFound: number;
  runsCount: number;
};

type MonitorChartRange = {
  bucketLabel: string;
  bucketSeconds: number | null;
  rangeEndMs: number;
  rangeStartMs: number;
};

type MonitorChartTooltipProps = {
  hoveredBucket: HoveredBucket;
  range: MonitorStatsRange;
};

type HoveredBucket = {
  align: 'center' | 'left' | 'right';
  point: MonitorChartPoint;
  x: number;
  y: number;
};

type ReferenceShapeProps = {
  height?: number;
  width?: number;
  x?: number;
  y?: number;
};

export default function MonitorPerformanceChart({
  chartData,
  chartDomain,
  chartRange,
  range,
  sessionStartedAtMs
}: {
  chartData: MonitorChartPoint[];
  chartDomain: [number, number] | undefined;
  chartRange: MonitorChartRange | null;
  range: MonitorStatsRange;
  sessionStartedAtMs: number | null;
}) {
  const [hoveredBucket, setHoveredBucket] = useState<HoveredBucket | null>(null);
  const ticks = useMemo(
    () => (chartRange ? chartTicks(chartRange, range) : undefined),
    [chartRange, range]
  );
  const boundaries = useMemo(() => bucketBoundaries(chartData), [chartData]);
  const xAxisLabel = chartRange ? `Tiempo - buckets ${chartRange.bucketLabel}` : 'Tiempo';
  const yMax = Math.max(1, ...chartData.map((point) => point.itemsFound));

  return (
    <div className="monitor-chart-canvas" onMouseLeave={() => setHoveredBucket(null)}>
      <div className="monitor-chart-legend" aria-hidden="true">
        <span />
        Encontrados
      </div>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart accessibilityLayer={false} data={chartData} margin={{ top: 24, right: 12, bottom: 28, left: 16 }}>
          <CartesianGrid strokeDasharray="3 3" vertical />
          <XAxis
            allowDataOverflow
            dataKey="bucketStartMs"
            domain={chartDomain}
            label={{ value: xAxisLabel, position: 'insideBottom', offset: -24 }}
            tickFormatter={(value) => formatChartTick(Number(value), range, chartRange)}
            ticks={ticks}
            type="number"
          />
          <YAxis
            allowDecimals={false}
            domain={[0, yMax]}
            label={{ value: 'Encontrados', angle: -90, position: 'insideLeft', offset: -12 }}
            width={44}
          />
          <Bar dataKey="itemsFound" fill="transparent" isAnimationActive={false} legendType="none" maxBarSize={0} />
          {chartData.map((point) =>
            point.itemsFound > 0 ? (
              <ReferenceArea
                className="monitor-interval-bar"
                fill="#2f7d6d"
                fillOpacity={1}
                ifOverflow="discard"
                key={`${point.bucketStartMs}-${point.bucketEndMs}-bar`}
                stroke="none"
                x1={point.bucketStartMs}
                x2={point.bucketEndMs}
                y1={0}
                y2={point.itemsFound}
              />
            ) : null
          )}
          {boundaries.map((boundary) => (
            <ReferenceLine
              className="monitor-chart-bucket-boundary"
              key={boundary}
              stroke="#dbe4ee"
              strokeWidth={1}
              x={boundary}
            />
          ))}
          {sessionStartedAtMs !== null ? (
            <ReferenceLine
              className="monitor-session-marker-svg"
              ifOverflow="discard"
              label={{
                fill: '#b42318',
                fontSize: 11,
                fontWeight: 700,
                position: sessionMarkerLabelPosition(sessionStartedAtMs, chartDomain),
                value: 'Inicio sesion'
              }}
              stroke="#ef4444"
              strokeWidth={2}
              x={sessionStartedAtMs}
            />
          ) : null}
          {chartData.map((point) => (
            <ReferenceArea
              className="monitor-interval-hit-area"
              fill="#ffffff"
              fillOpacity={0}
              ifOverflow="discard"
              key={`${point.bucketStartMs}-${point.bucketEndMs}-hit`}
              shape={renderHitAreaShape(point, setHoveredBucket)}
              stroke="none"
              x1={point.bucketStartMs}
              x2={point.bucketEndMs}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
      {hoveredBucket !== null ? <MonitorChartTooltip hoveredBucket={hoveredBucket} range={range} /> : null}
    </div>
  );
}

function handleBucketHover(point: MonitorChartPoint, onHover: (hoveredBucket: HoveredBucket | null) => void) {
  return (event: MouseEvent<SVGElement>) => {
    const targetRect = event.currentTarget.getBoundingClientRect();
    const canvasRect = event.currentTarget.ownerSVGElement?.closest('.monitor-chart-canvas')?.getBoundingClientRect();
    if (!canvasRect) {
      return;
    }
    const x = targetRect.left - canvasRect.left + targetRect.width / 2;
    const y = Math.min(canvasRect.height - 10, targetRect.top - canvasRect.top + targetRect.height / 2);
    const align = x > canvasRect.width * 0.78 ? 'left' : x < canvasRect.width * 0.22 ? 'right' : 'center';
    onHover({ align, point, x, y });
  };
}

function sessionMarkerLabelPosition(sessionStartedAtMs: number, chartDomain: [number, number] | undefined): 'insideTopLeft' | 'insideTopRight' {
  if (!chartDomain) {
    return 'insideTopLeft';
  }
  const [start, end] = chartDomain;
  const ratio = (sessionStartedAtMs - start) / (end - start);
  return ratio > 0.86 ? 'insideTopRight' : 'insideTopLeft';
}

function renderHitAreaShape(point: MonitorChartPoint, onHover: (hoveredBucket: HoveredBucket | null) => void) {
  return ({ height = 0, width = 0, x = 0, y = 0 }: ReferenceShapeProps) => (
    <rect
      className="monitor-interval-hit-rect"
      fill="transparent"
      height={height}
      pointerEvents="all"
      width={width}
      x={x}
      y={y}
      onMouseEnter={handleBucketHover(point, onHover)}
      onMouseMove={handleBucketHover(point, onHover)}
    />
  );
}

function MonitorChartTooltip({ hoveredBucket, range }: MonitorChartTooltipProps) {
  const point = hoveredBucket.point;
  const className = `monitor-chart-tooltip align-${hoveredBucket.align}`;

  return (
    <div className={className} style={{ left: hoveredBucket.x, top: hoveredBucket.y }}>
      <strong>{formatChartTooltip(point, range)}</strong>
      <span>Encontrados: {point.itemsFound}</span>
      <span>Ejecuciones: {point.runsCount}</span>
    </div>
  );
}

function bucketBoundaries(chartData: MonitorChartPoint[]): number[] {
  const boundaries = new Set<number>();
  for (const point of chartData) {
    boundaries.add(point.bucketStartMs);
    boundaries.add(point.bucketEndMs);
  }
  return Array.from(boundaries).sort((left, right) => left - right);
}

function chartTicks(chartRange: MonitorChartRange, range: MonitorStatsRange): number[] {
  if (range === 'minutes') {
    return steppedTicks(chartRange.rangeStartMs, chartRange.rangeEndMs, 5_000);
  }
  if (range === 'hours') {
    return steppedTicks(chartRange.rangeStartMs, chartRange.rangeEndMs, 15 * 60_000);
  }
  if (range === 'days') {
    return steppedTicks(chartRange.rangeStartMs, chartRange.rangeEndMs, 3 * 60 * 60_000);
  }
  if (range === 'month') {
    return monthTicks(chartRange.rangeStartMs, chartRange.rangeEndMs);
  }
  return allRangeTicks(chartRange);
}

function steppedTicks(start: number, end: number, stepMs: number): number[] {
  const ticks: number[] = [];
  for (let current = start; current <= end; current += stepMs) {
    ticks.push(current);
  }
  return ticks;
}

function monthTicks(start: number, end: number): number[] {
  const startDate = new Date(start);
  const endDate = new Date(end);
  const days = [1, 5, 10, 15, 20, 25];
  const ticks = days
    .map((day) => Date.UTC(startDate.getUTCFullYear(), startDate.getUTCMonth(), day))
    .filter((tick) => tick >= start && tick < end);
  ticks.push(Date.UTC(endDate.getUTCFullYear(), endDate.getUTCMonth(), 1));
  return ticks;
}

function allRangeTicks(chartRange: MonitorChartRange): number[] {
  if (chartRange.bucketSeconds === 300) {
    return steppedTicks(chartRange.rangeStartMs, chartRange.rangeEndMs, 15 * 60_000);
  }
  if (chartRange.bucketSeconds === 3600) {
    return steppedTicks(chartRange.rangeStartMs, chartRange.rangeEndMs, 6 * 60 * 60_000);
  }
  if (chartRange.bucketSeconds === 86400) {
    return steppedTicks(chartRange.rangeStartMs, chartRange.rangeEndMs, 14 * 24 * 60 * 60_000);
  }
  return steppedTicks(chartRange.rangeStartMs, chartRange.rangeEndMs, 90 * 24 * 60 * 60_000);
}

function formatChartTick(value: number, range: MonitorStatsRange, chartRange: MonitorChartRange | null): string {
  const date = new Date(value);
  if (range === 'minutes') {
    if (value === chartRange?.rangeEndMs) {
      return '60';
    }
    return pad2(date.getUTCSeconds());
  }
  if (range === 'hours') {
    return date.getUTCMinutes() === 0 && value === chartRange?.rangeEndMs ? '60' : pad2(date.getUTCMinutes());
  }
  if (range === 'days') {
    return value === chartRange?.rangeEndMs ? '24' : pad2(date.getUTCHours());
  }
  if (range === 'month') {
    return String(date.getUTCDate());
  }
  if (range === 'all') {
    if (chartRange?.bucketSeconds === 300 || chartRange?.bucketSeconds === 3600) {
      return `${pad2(date.getUTCHours())}:${pad2(date.getUTCMinutes())}`;
    }
    if (chartRange?.bucketSeconds === 86400) {
      return `${pad2(date.getUTCDate())} ${monthShortUtc(date)}`;
    }
    return `${monthShortUtc(date)} ${String(date.getUTCFullYear()).slice(2)}`;
  }
  return `${pad2(date.getUTCDate())} ${monthShortUtc(date)}`;
}

function pad2(value: number): string {
  return String(value).padStart(2, '0');
}

function monthShortUtc(date: Date): string {
  return date.toLocaleDateString([], { month: 'short', timeZone: 'UTC' });
}

function formatChartTooltip(point: MonitorChartPoint, range: MonitorStatsRange): string {
  const start = new Date(point.bucketStartMs);
  const end = new Date(point.bucketEndMs);
  const bucketMs = point.bucketEndMs - point.bucketStartMs;
  if (range === 'minutes' || range === 'hours') {
    return `${start.toLocaleString([], { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' })} - ${end.toLocaleString([], {
      day: '2-digit',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    })}`;
  }
  if (bucketMs < 24 * 60 * 60_000) {
    return `${start.toLocaleString([], { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })} - ${end.toLocaleString([], {
      day: '2-digit',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit'
    })}`;
  }
  if (range === 'all') {
    return `${start.toLocaleDateString([], { day: '2-digit', month: 'short', year: 'numeric' })} - ${end.toLocaleDateString([], {
      day: '2-digit',
      month: 'short',
      year: 'numeric'
    })}`;
  }
  return `${start.toLocaleDateString([], { day: '2-digit', month: 'short' })} - ${end.toLocaleDateString([], {
    day: '2-digit',
    month: 'short'
  })}`;
}
