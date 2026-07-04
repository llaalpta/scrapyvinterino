import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
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
        <BarChart data={chartData} margin={{ top: 8, right: 14, bottom: 34, left: 22 }}>
          <CartesianGrid strokeDasharray="3 3" vertical />
          <XAxis
            dataKey="bucketStartMs"
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
  const point = chartData.find((entry) => entry.bucketStartMs === label);
  if (!point) {
    return null;
  }

  return (
    <div className="monitor-chart-tooltip">
      <strong>{formatChartTooltip(label, range, chartData)}</strong>
      <span>Encontrados: {point.itemsFound}</span>
      <span>Ejecuciones: {point.runsCount}</span>
    </div>
  );
}

function chartTicks(chartRange: MonitorChartRange, range: MonitorStatsRange): number[] {
  const stepMs = tickStepMs(range, chartRange);
  const ticks: number[] = [];
  let current = alignTickStart(chartRange.rangeStartMs, stepMs);
  if (current < chartRange.rangeStartMs) {
    current += stepMs;
  }
  while (current <= chartRange.rangeEndMs) {
    ticks.push(current);
    current += stepMs;
  }
  return ticks.length > 0 ? ticks : [chartRange.rangeStartMs, chartRange.rangeEndMs];
}

function tickStepMs(range: MonitorStatsRange, chartRange: MonitorChartRange): number {
  if (range === 'minutes') {
    return 60_000;
  }
  if (range === 'hours') {
    return 15 * 60_000;
  }
  if (range === 'days') {
    return 6 * 60 * 60_000;
  }
  if (range === 'month') {
    return 7 * 24 * 60 * 60_000;
  }
  if (chartRange.bucketSeconds === 300) {
    return 15 * 60_000;
  }
  if (chartRange.bucketSeconds === 3600) {
    return 6 * 60 * 60_000;
  }
  if (chartRange.bucketSeconds === 86400) {
    return 14 * 24 * 60 * 60_000;
  }
  return 90 * 24 * 60 * 60_000;
}

function alignTickStart(value: number, stepMs: number): number {
  return Math.floor(value / stepMs) * stepMs;
}

function formatChartTick(value: number, range: MonitorStatsRange, chartRange: MonitorChartRange | null): string {
  const date = new Date(value);
  if (range === 'minutes' || range === 'hours' || range === 'days') {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  if (range === 'all') {
    if (chartRange?.bucketSeconds === 300 || chartRange?.bucketSeconds === 3600) {
      return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
    if (chartRange?.bucketSeconds === 86400) {
      return date.toLocaleDateString([], { day: '2-digit', month: 'short' });
    }
    return date.toLocaleDateString([], { month: 'short', year: '2-digit' });
  }
  return date.toLocaleDateString([], { day: '2-digit', month: 'short' });
}

function formatChartTooltip(value: number, range: MonitorStatsRange, chartData: MonitorChartPoint[]): string {
  const point = chartData.find((entry) => entry.bucketStartMs === value);
  const start = new Date(value);
  const end = point ? new Date(point.bucketEndMs) : start;
  const bucketMs = point ? point.bucketEndMs - point.bucketStartMs : 0;
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
