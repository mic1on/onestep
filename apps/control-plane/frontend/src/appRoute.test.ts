import { describe, expect, it } from 'vitest';
import { createAppRoutePath, parseAppRoute } from './appRoute';

describe('app route helpers', () => {
  it('restores service tabs from the URL', () => {
    expect(parseAppRoute('/services?tab=instances')).toEqual({
      currentView: 'services',
      activeTab: 'Instances',
      selectedTaskId: null,
    });
    expect(createAppRoutePath({ currentView: 'services', activeTab: 'Instances', selectedTaskId: null })).toBe(
      '/services?tab=instances',
    );
  });

  it('restores top-level views from the URL', () => {
    expect(parseAppRoute('/notifications')).toEqual({
      currentView: 'notifications',
      activeTab: 'Tasks',
      selectedTaskId: null,
    });
    expect(createAppRoutePath({ currentView: 'overview', activeTab: 'Tasks', selectedTaskId: null })).toBe(
      '/overview',
    );
  });

  it('round-trips task detail routes', () => {
    const route = {
      currentView: 'services' as const,
      activeTab: 'Tasks' as const,
      selectedTaskId: 'orders/to-ledger',
    };

    expect(createAppRoutePath(route)).toBe('/services/tasks/orders%2Fto-ledger');
    expect(parseAppRoute('/services/tasks/orders%2Fto-ledger')).toEqual(route);
  });
});
