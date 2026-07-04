import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { MonitorStatsRange } from '../../api';

type MonitorChartPoint = {
  bucketEndMs: number;
  bucketMidMs: number;
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
  active?: boolean;
  chartData: MonitorChartPoint[];
  label?: unknown;
  range: MonitorStatsRange;
};

const plotOverlayInset = {
  bottom: 64,
  left: 66,
  right: 14,
  top: 32
};

export default function MonitorPerformanceChart({
  chartData,
  chartDomain,
  chartRange,
  range,
  sessionMarkerClass,
  sessionMarkerPosition
}: {
  chartData: MonitorChartPoint[];
  chartDomain: [number, number] | undefined;
  chartRange: MonitorChartRange | null;
  range: MonitorStatsRange;
  sessionMarkerClass: string;
  sessionMarkerPosition: number | null;
}) {
  const ticks = chartRange ? chartTicks(chartRange, range) : undefined;
  const xAxisLabel = chartRange ? `Tiempo - buckets ${chartRange.bucketLabel}` : 'Tiempo';

  return (
    <div className="monitor-chart-canvas">
      <ResponsiveContainer width="100%" height={260}>
        <BarChart accessibilityLayer={false} barCategoryGap={0} barGap={0} data={chartData} margin={{ top: 8, right: 14, bottom: 34, left: 22 }}>
          <CartesianGrid strokeDasharray="3 3" vertical />
          <XAxis
            dataKey="bucketMidMs"
            domain={chartDomain}
            label={{ value: xAxisLabel, position: 'insideBottom', offset: -24 }}
            tickFormatter={(value) => formatChartTick(Number(value), range, chartRange)}
            ticks={ticks}
            type="number"
          />
          <YAxis
            allowDecimals={false}
            label={{ value: 'Encontrados', angle: -90, position: 'insideLeft', offset: -12 }}
            width={44}
          />
          <Tooltip content={<MonitorChartTooltip chartData={chartData} range={range} />} />
          <Legend align="right" height={24} verticalAlign="top" />
          <Bar dataKey="itemsFound" fill="#2f7d6d" name="Encontrados" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
      {sessionMarkerPosition !== null ? (
        <div className="monitor-plot-overlay" style={plotOverlayInset} aria-hidden="true">
          <div className={sessionMarkerClass} style={{ left: `${sessionMarkerPosition * 100}%` }}>
            <span>Inicio sesion</span>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function MonitorChartTooltip({
  active,
  chartData,
  label,
  range
}: MonitorChartTooltipProps) {
  if (!active || typeof label !== 'number') {
    return null;
  }
  const point = chartData.find((entry) => entry.bucketMidMs === label);
  if (!point) {
    return null;
  }

  return (
    <div className="monitor-chart-tooltip">
      <strong>{formatChartTooltip(point, range)}</strong>
      <span>Encontrados: {point.itemsFound}</span>
      <span>Ejecuciones: {point.runsCount}</span>
    </div>
  );
}

function chartTicks(chartRange: MonitorChartRange, range: MonitorStatsRange): number[] {
  if (range === 'minutes') {
    return steppedTicks(chartRange.rangeStartMs, chartRange.rangeEndMs, 10_000);
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
