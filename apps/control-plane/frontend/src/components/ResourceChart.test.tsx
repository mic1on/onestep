import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { TaskMetricChartPointSummary } from '../api';
import { I18nProvider } from '../i18n';
import ResourceChart from './ResourceChart';

const zeroFailureWindows: TaskMetricChartPointSummary[] = [
  {
    bucket_started_at: '2026-07-18T14:00:00Z',
    bucket_ended_at: '2026-07-18T14:01:00Z',
    reported_window_count: 1,
    fetched: 1,
    started: 1,
    succeeded: 1,
    retried: 0,
    failed: 0,
    dead_lettered: 0,
    cancelled: 0,
    timeouts: 0,
    inflight: 0,
    avg_duration_ms: 10,
    p95_duration_ms: 10,
  },
  {
    bucket_started_at: '2026-07-18T14:01:00Z',
    bucket_ended_at: '2026-07-18T14:02:00Z',
    reported_window_count: 0,
    fetched: 0,
    started: 0,
    succeeded: 0,
    retried: 0,
    failed: 0,
    dead_lettered: 0,
    cancelled: 0,
    timeouts: 0,
    inflight: 0,
    avg_duration_ms: null,
    p95_duration_ms: null,
  },
];

function renderChart(
  windows: TaskMetricChartPointSummary[],
  lookbackMinutes = 15,
  onLookbackMinutesChange = vi.fn(),
) {
  return render(
    <I18nProvider initialLocale="en">
      <ResourceChart
        windows={windows}
        lookbackMinutes={lookbackMinutes}
        onLookbackMinutesChange={onLookbackMinutesChange}
      />
    </I18nProvider>,
  );
}

describe('ResourceChart', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('does not render the failure series in all mode when failures are zero', () => {
    const { container } = renderChart(zeroFailureWindows);

    expect(container.querySelector('[stroke="#f43f5e"]')).toBeNull();
    expect(container.querySelector('[fill="#f43f5e"]')).toBeNull();
  });

  it('uses unique integer Y-axis labels for low count ranges', () => {
    const { container } = renderChart(zeroFailureWindows);

    const labels = Array.from(container.querySelectorAll('g.opacity-15 text')).map(
      (node) => node.textContent,
    );
    expect(labels).toEqual(['0', '1']);
  });

  it('uses a wide chart viewport so desktop cards do not leave large side gutters', () => {
    const { container } = renderChart(zeroFailureWindows);

    const svg = container.querySelector('svg.w-full');
    const gridLine = container.querySelector('g.opacity-15 line');

    expect(svg?.getAttribute('viewBox')).toBe('0 0 900 220');
    expect(gridLine?.getAttribute('x2')).toBe('856');
  });

  it('changes the metric lookback from a preset control', () => {
    const handleLookbackChange = vi.fn();
    const { getByRole } = renderChart(zeroFailureWindows, 15, handleLookbackChange);

    fireEvent.click(getByRole('button', { name: '30m' }));

    expect(handleLookbackChange).toHaveBeenCalledWith(30);
  });

  it('changes the metric lookback from the custom minutes input', () => {
    const handleLookbackChange = vi.fn();
    const { getByLabelText, getByRole } = renderChart(zeroFailureWindows, 15, handleLookbackChange);

    fireEvent.change(getByLabelText('Lookback minutes'), { target: { value: '45' } });
    fireEvent.click(getByRole('button', { name: 'Apply' }));

    expect(handleLookbackChange).toHaveBeenCalledWith(45);
  });

  it('keeps the hover tooltip inside the chart instead of pinning it to the card bottom', () => {
    const { container, getByRole } = renderChart(zeroFailureWindows);
    const hoverTargets = container.querySelectorAll('svg rect.cursor-pointer');

    fireEvent.mouseEnter(hoverTargets[0]);

    const tooltip = getByRole('tooltip');
    const tooltipStyle = tooltip.getAttribute('style') ?? '';
    expect(tooltipStyle).toContain('top:');
    expect(tooltipStyle).not.toContain('bottom: 40px');
  });

  it('announces metric loading without changing the chart frame', () => {
    render(
      <I18nProvider initialLocale="en">
        <ResourceChart
          error={null}
          isLoading
          lookbackMinutes={15}
          onLookbackMinutesChange={vi.fn()}
          windows={[]}
        />
      </I18nProvider>,
    );

    expect(screen.getByRole('status').textContent).toContain('Loading task metrics...');
  });
});
