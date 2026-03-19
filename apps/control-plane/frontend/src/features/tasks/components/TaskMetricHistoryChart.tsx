import { useTranslation } from "react-i18next";

import type { TaskMetricWindowSummary } from "../../../lib/api/types";
import { formatCount, formatDateTime, formatDurationMs } from "../../../lib/formatters";
import { getCurrentIntlLocale } from "../../../lib/i18n";

type TaskMetricHistoryChartProps = {
  windows: TaskMetricWindowSummary[];
};

const CHART_WIDTH = 760;
const CHART_HEIGHT = 272;
const CHART_MARGIN = {
  top: 28,
  right: 28,
  bottom: 42,
  left: 34,
};

export function TaskMetricHistoryChart({ windows }: TaskMetricHistoryChartProps) {
  const { t } = useTranslation();
  const sortedWindows = [...windows].sort(
    (left, right) => Date.parse(left.window_started_at) - Date.parse(right.window_started_at),
  );

  if (sortedWindows.length === 0) {
    return null;
  }

  const firstWindow = sortedWindows[0];
  const lastWindow = sortedWindows[sortedWindows.length - 1];
  const plotWidth = CHART_WIDTH - CHART_MARGIN.left - CHART_MARGIN.right;
  const plotHeight = CHART_HEIGHT - CHART_MARGIN.top - CHART_MARGIN.bottom;
  const slotWidth = plotWidth / sortedWindows.length;
  const barWidth = Math.max(14, Math.min(38, slotWidth * 0.58));
  const maxVolume = Math.max(
    ...sortedWindows.map((window) => window.succeeded + window.failed + window.dead_lettered),
    1,
  );
  const maxP95 = Math.max(...sortedWindows.map((window) => window.p95_duration_ms ?? 0), 1);
  const volumeTicks = [1, 0.66, 0.33, 0].map((ratio) => Math.round(maxVolume * ratio));
  const tickIndices = getTickIndices(sortedWindows.length, 4);
  const p95Path = sortedWindows
    .map((window, index) => {
      if (window.p95_duration_ms === null) {
        return null;
      }

      const centerX = CHART_MARGIN.left + slotWidth * index + slotWidth / 2;
      const y = getP95Y(window.p95_duration_ms, maxP95, plotHeight);
      return `${index === 0 || sortedWindows[index - 1].p95_duration_ms === null ? "M" : "L"} ${centerX} ${y}`;
    })
    .filter((segment): segment is string => segment !== null)
    .join(" ");

  return (
    <section className="task-history-chart">
      <div className="task-history-chart-topline">
        <div className="task-history-legend">
          <span className="task-history-legend-item">
            <span className="task-history-legend-swatch is-success" />
            {t("taskDetail.succeeded")}
          </span>
          <span className="task-history-legend-item">
            <span className="task-history-legend-swatch is-danger" />
            {t("taskDetail.failedDlq")}
          </span>
          <span className="task-history-legend-item">
            <span className="task-history-legend-swatch is-latency" />
            p95
          </span>
        </div>
        <span className="task-history-chart-range">
          {t("taskDetail.windowRange", {
            start: formatDateTime(firstWindow.window_started_at),
            end: formatDateTime(lastWindow.window_ended_at),
          })}
        </span>
      </div>

      <div className="task-history-chart-frame">
        <svg
          aria-label={t("taskDetail.recentWindowsTitle")}
          role="img"
          viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
        >
          {volumeTicks.map((tick) => {
            const y = CHART_MARGIN.top + plotHeight - (tick / maxVolume) * plotHeight;
            return (
              <g key={tick}>
                <line
                  className="task-history-grid-line"
                  x1={CHART_MARGIN.left}
                  x2={CHART_WIDTH - CHART_MARGIN.right}
                  y1={y}
                  y2={y}
                />
                <text className="task-history-axis-label" textAnchor="start" x={4} y={y - 6}>
                  {formatCount(tick)}
                </text>
              </g>
            );
          })}

          <text className="task-history-axis-label" textAnchor="end" x={CHART_WIDTH - 4} y={CHART_MARGIN.top - 8}>
            {formatDurationMs(maxP95)}
          </text>

          {sortedWindows.map((window, index) => {
            const centerX = CHART_MARGIN.left + slotWidth * index + slotWidth / 2;
            const barX = centerX - barWidth / 2;
            const issues = window.failed + window.dead_lettered;
            const successHeight = (window.succeeded / maxVolume) * plotHeight;
            const issueHeight = (issues / maxVolume) * plotHeight;
            const successY = CHART_MARGIN.top + plotHeight - successHeight;
            const issuesY = successY - issueHeight;

            return (
              <g key={window.window_id}>
                <title>
                  {buildWindowTooltip(t, window)}
                </title>

                {window.succeeded > 0 ? (
                  <rect
                    className="task-history-bar-success"
                    height={successHeight}
                    rx={10}
                    ry={10}
                    width={barWidth}
                    x={barX}
                    y={successY}
                  />
                ) : null}

                {issues > 0 ? (
                  <rect
                    className="task-history-bar-danger"
                    height={issueHeight}
                    rx={10}
                    ry={10}
                    width={barWidth}
                    x={barX}
                    y={issuesY}
                  />
                ) : null}

                {tickIndices.includes(index) ? (
                  <text
                    className="task-history-tick-label"
                    textAnchor="middle"
                    x={centerX}
                    y={CHART_HEIGHT - 10}
                  >
                    {formatTickLabel(window.window_started_at, firstWindow.window_started_at, lastWindow.window_ended_at)}
                  </text>
                ) : null}
              </g>
            );
          })}

          {p95Path ? <path className="task-history-line" d={p95Path} /> : null}

          {sortedWindows.map((window, index) => {
            if (window.p95_duration_ms === null) {
              return null;
            }

            const centerX = CHART_MARGIN.left + slotWidth * index + slotWidth / 2;
            const y = getP95Y(window.p95_duration_ms, maxP95, plotHeight);

            return <circle className="task-history-point" cx={centerX} cy={y} key={window.window_id} r={4} />;
          })}
        </svg>
      </div>
    </section>
  );
}

function getP95Y(value: number, maxP95: number, plotHeight: number) {
  return CHART_MARGIN.top + plotHeight - (value / maxP95) * plotHeight;
}

function getTickIndices(count: number, maxTicks: number) {
  if (count <= maxTicks) {
    return Array.from({ length: count }, (_, index) => index);
  }

  const indices = new Set<number>([0, count - 1]);
  const step = (count - 1) / (maxTicks - 1);
  for (let index = 1; index < maxTicks - 1; index += 1) {
    indices.add(Math.round(step * index));
  }

  return [...indices].sort((left, right) => left - right);
}

function formatTickLabel(value: string, rangeStart: string, rangeEnd: string) {
  const locale = getCurrentIntlLocale();
  const spanMs = Date.parse(rangeEnd) - Date.parse(rangeStart);

  return new Intl.DateTimeFormat(locale, {
    ...(spanMs > 24 * 60 * 60 * 1000
      ? { month: "short", day: "numeric", hour: "2-digit" as const }
      : { hour: "2-digit", minute: "2-digit" as const }),
  }).format(new Date(value));
}

function buildWindowTooltip(
  t: ReturnType<typeof useTranslation>["t"],
  window: TaskMetricWindowSummary,
) {
  const issues = window.failed + window.dead_lettered;

  return [
    window.window_id,
    t("taskDetail.windowRange", {
      start: formatDateTime(window.window_started_at),
      end: formatDateTime(window.window_ended_at),
    }),
    t("metrics.ok", { value: formatCount(window.succeeded) }),
    `${t("taskDetail.failedDlq")}: ${formatCount(issues)}`,
    `p95 ${formatDurationMs(window.p95_duration_ms)}`,
  ].join(" · ");
}
