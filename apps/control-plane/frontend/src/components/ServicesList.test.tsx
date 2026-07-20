import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../i18n';
import type { Service } from '../types';
import ServicesList from './ServicesList';

function service(overrides: Partial<Service>): Service {
  return {
    id: 'billing-sync:prod',
    name: 'billing-sync / prod',
    viewStatus: 'running',
    uptimeReferenceAt: null,
    throughputPerMin: 48,
    successRate: 100,
    errorCount: 0,
    totalInstances: 1,
    activeInstances: 1,
    standbyInstances: 0,
    totalTaskCount: 1,
    failingTaskCount: 0,
    onlineTaskCount: 1,
    ...overrides,
  };
}

function renderServicesList(services: Service[]) {
  return render(
    <I18nProvider initialLocale="en">
      <ServicesList services={services} onSelectService={vi.fn()} />
    </I18nProvider>,
  );
}

describe('ServicesList filters', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('renders the services title without the standalone breadcrumb label', () => {
    renderServicesList([service({})]);

    expect(screen.getByRole('heading', { name: 'Service Directory' })).toBeTruthy();
    expect(screen.queryByText('Services')).toBeNull();
  });

  it('filters services by display status', async () => {
    const user = userEvent.setup();
    renderServicesList([
      service({ id: 'billing-sync:prod', name: 'billing-sync / prod' }),
      service({
        id: 'audit-sync:prod',
        name: 'audit-sync / prod',
        viewStatus: 'stopped',
        activeInstances: 0,
        successRate: 0,
        onlineTaskCount: 0,
      }),
    ]);

    await user.click(screen.getByRole('button', { name: 'Status: All' }));
    await user.click(screen.getByRole('button', { name: 'OFFLINE' }));

    expect(screen.getByText('audit-sync / prod')).toBeTruthy();
    expect(screen.queryByText('billing-sync / prod')).toBeNull();
  });
});
