import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { I18nProvider } from '../i18n';
import type { Task } from '../types';
import ConfigEditor from './ConfigEditor';

const baseTask: Task = {
  id: 'svc:dev:produce_and_store',
  apiName: 'produce_and_store',
  apiServiceName: 'svc',
  environment: 'dev',
  serviceId: 'svc:dev',
  name: 'produce_and_store',
  viewStatus: 'running',
  supportedCommands: [],
  pipelineSource: 'interval',
  pipelineSourceLabel: 'interval:5s',
  sourceKind: 'interval',
  sourceConfig: { seconds: 5 },
  sourceName: 'interval',
  pipelineSink: 'mysql_table_sink',
  pipelineSinkLabel: 'mysql.table_sink:cp_demo_events',
  sinkKind: 'mysql_table_sink',
  sinkConfig: { table: 'cp_demo_events' },
  sinkName: 'mysql.table_sink:cp_demo_events',
  concurrency: 1,
  retryAttempts: 3,
  uptimeReferenceAt: null,
  throughputPerMin: 120,
  successRate: 100,
  errorCount: 0,
  configYaml: '',
};

function renderConfigEditor(configYaml: string) {
  return render(
    <I18nProvider initialLocale="en">
      <ConfigEditor task={{ ...baseTask, configYaml }} />
    </I18nProvider>,
  );
}

describe('ConfigEditor', () => {
  it('renders YAML lines without collapsing indentation or wrapping long values', () => {
    renderConfigEditor(
      [
        'task_config:',
        '  id: "produce_and_store"',
        '  topology_hash:',
        '    "sha256:6c0e7cdb2df50a0c2e5955d03ce547be6a5544c97f60d9af8f0218730bb2c4b6"',
        'execution:',
        '  concurrency: 1',
      ].join('\n'),
    );

    const lines = screen.getAllByTestId('config-yaml-line');
    const lineContents = screen.getAllByTestId('config-yaml-line-content');

    expect(lines).toHaveLength(6);
    expect(lines[2].className).toContain('min-w-max');
    expect(lineContents[2].className).toContain('whitespace-pre');
    expect(lineContents[2].textContent).toBe('  topology_hash:');
    expect(lineContents[3].className).toContain('whitespace-pre');
    expect(lineContents[3].textContent).toMatch(/^    "sha256:/);
  });
});
