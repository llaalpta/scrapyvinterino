import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { MonitorStatsRange } from '../../api';

type MonitorChartPoint = {
  bucketEndMs: number;
  bucketStartMs: number;
  itemsFound: number;
  runsCount: number;
};

export default function MonitorPerformanceChart({
  chartData,
  chartDomain,
  range,
  sessionMarkerClass,
  sessionMarkerPosition
}: {
  chartData: MonitorChartPoint[];
  chartDomain: [number, number] | undefined;
  range: MonitorStatsRange;
  sessionMarkerClass: string;
  sessionMarkerPosition: number | null;
}) {
  return (
    <div className="monitor-chart-canvas">
      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={chartData} margin={{ top: 14, right: 10, bottom: 4, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="bucketStartMs" domain={chartDomain} tickFormatter={(value) => formatChartTick(Number(value), range)} type="number" />
          <YAxis allowDecimals={false} width={34} />
          <Tooltip
            formatter={(value, name) => [String(value), name === 'itemsFound' ? 'Encontrados' : 'Runs']}
            labelFormatter={(value) => formatChartTooltip(Number(value), range)}
          />
          <Bar dataKey="itemsFound" fill="#2f7d6d" name="Encontrados" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
      {sessionMarkerPosition !== null ? (
        <div className="monitor-plot-overlay" aria-hidden="true">
          <div className={sessionMarkerClass} style={{ left: `${sessionMarkerPosition * 100}%` }}>
            <span>Inicio sesion</span>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function formatChartTick(value: number, range: MonitorStatsRange): string {
  const date = new Date(value);
  if (range === 'minutes' || range === 'hours') {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  if (range === 'all') {
    return date.toLocaleDateString([], { month: 'short', year: '2-digit' });
  }
  return date.toLocaleDateString([], { day: '2-digit', month: 'short' });
}

function formatChartTooltip(value: number, range: MonitorStatsRange): string {
  const date = new Date(value);
  if (range === 'minutes' || range === 'hours') {
    return date.toLocaleString([], { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
  }
  if (range === 'all') {
    return date.toLocaleDateString([], { month: 'long', year: 'numeric' });
  }
  return date.toLocaleDateString([], { day: '2-digit', month: 'long', year: 'numeric' });
}
