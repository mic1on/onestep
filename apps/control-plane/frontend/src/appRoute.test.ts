import { describe, expect, it } from 'vitest';
import { createAppRoutePath, parseAppRoute } from './appRoute';

describe('app route helpers', () => {
  it('parses bare /services as the services list view', () => {
    expect(parseAppRoute('/services')).toEqual({
      currentView: 'servicesList',
      selectedServiceId: null,
      activeTab: 'Tasks',
      selectedTaskId: null,
    });
    expect(createAppRoutePath({ currentView: 'servicesList', selectedServiceId: null, activeTab: 'Tasks', selectedTaskId: null })).toBe(
      '/services',
    );
  });

  it('restores service detail tabs from the URL', () => {
    expect(parseAppRoute('/services/orders-svc?tab=instances')).toEqual({
      currentView: 'services',
      selectedServiceId: 'orders-svc',
      activeTab: 'Instances',
      selectedTaskId: null,
    });
    expect(
      createAppRoutePath({
        currentView: 'services',
        selectedServiceId: 'orders-svc',
        activeTab: 'Instances',
        selectedTaskId: null,
      }),
    ).toBe('/services/orders-svc?tab=instances');
  });

  it('falls back to tasks for legacy service configuration tab URLs', () => {
    expect(parseAppRoute('/services/orders-svc?tab=configuration')).toEqual({
      currentView: 'services',
      selectedServiceId: 'orders-svc',
      activeTab: 'Tasks',
      selectedTaskId: null,
    });
    expect(parseAppRoute('/services/orders-svc?tab=config')).toEqual({
      currentView: 'services',
      selectedServiceId: 'orders-svc',
      activeTab: 'Tasks',
      selectedTaskId: null,
    });
  });

  it('restores top-level views from the URL', () => {
    expect(parseAppRoute('/notifications')).toEqual({
      currentView: 'notifications',
      selectedServiceId: null,
      activeTab: 'Tasks',
      selectedTaskId: null,
    });
    expect(createAppRoutePath({ currentView: 'overview', selectedServiceId: null, activeTab: 'Tasks', selectedTaskId: null })).toBe(
      '/overview',
    );
  });

  it('round-trips task detail routes scoped under a service', () => {
    const route = {
      currentView: 'services' as const,
      selectedServiceId: 'orders-svc',
      activeTab: 'Tasks' as const,
      selectedTaskId: 'orders/to-ledger',
    };

    expect(createAppRoutePath(route)).toBe('/services/orders-svc/tasks/orders%2Fto-ledger');
    expect(parseAppRoute('/services/orders-svc/tasks/orders%2Fto-ledger')).toEqual(route);
  });
});
