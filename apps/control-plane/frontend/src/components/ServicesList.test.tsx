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
    description: null,
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

function renderServicesList(
  services: Service[],
  onSelectService: (serviceId: string) => void = vi.fn(),
) {
  return render(
    <I18nProvider initialLocale="en">
      <ServicesList services={services} onSelectService={onSelectService} />
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

  it('labels service detail destinations without changing row selection', async () => {
    const user = userEvent.setup();
    const onSelectService = vi.fn();
    renderServicesList([service({})], onSelectService);

    expect(screen.getByText('Service Detail')).toBeTruthy();
    expect(screen.queryByText('View Task Details')).toBeNull();

    await user.click(screen.getByText('Service Detail'));

    expect(onSelectService).toHaveBeenCalledWith('billing-sync:prod');
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

  it('does not render the redundant service id line', () => {
    renderServicesList([service({ id: 'billing-sync:prod', name: 'billing-sync / prod' })]);

    expect(screen.getByText('billing-sync / prod')).toBeTruthy();
    expect(screen.queryByText('billing-sync:prod')).toBeNull();
  });

  it('renders and searches service descriptions', async () => {
    const user = userEvent.setup();
    renderServicesList([
      service({
        id: 'billing-sync:prod',
        name: 'billing-sync / prod',
        description: 'Reconciles invoices into the warehouse',
      }),
      service({
        id: 'audit-sync:prod',
        name: 'audit-sync / prod',
        description: 'Ships audit events',
      }),
    ]);

    expect(screen.getByText('Reconciles invoices into the warehouse')).toBeTruthy();

    await user.type(screen.getByPlaceholderText('Search by service name or ID'), 'invoices');

    expect(screen.getByText('billing-sync / prod')).toBeTruthy();
    expect(screen.queryByText('audit-sync / prod')).toBeNull();
  });
});
