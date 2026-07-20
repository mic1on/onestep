export type ControlPlaneView = 'overview' | 'servicesList' | 'services' | 'notifications';
export type ServiceTab = 'Tasks' | 'Instances' | 'Logs';

export interface AppRouteState {
  currentView: ControlPlaneView;
  selectedServiceId: string | null;
  activeTab: ServiceTab;
  selectedTaskId: string | null;
}

const DEFAULT_ROUTE_STATE: AppRouteState = {
  currentView: 'servicesList',
  selectedServiceId: null,
  activeTab: 'Tasks',
  selectedTaskId: null,
};

const tabToParam: Record<ServiceTab, string> = {
  Tasks: 'tasks',
  Instances: 'instances',
  Logs: 'logs',
};

const paramToTab: Record<string, ServiceTab> = {
  tasks: 'Tasks',
  instances: 'Instances',
  logs: 'Logs',
};

export function parseAppRoute(pathWithSearch: string): AppRouteState {
  const url = new URL(pathWithSearch, 'http://onestep.local');
  const pathname = url.pathname.replace(/\/+$/, '') || '/';

  if (pathname === '/overview') {
    return { ...DEFAULT_ROUTE_STATE, currentView: 'overview' };
  }

  if (pathname === '/notifications') {
    return { ...DEFAULT_ROUTE_STATE, currentView: 'notifications' };
  }

  // Task detail: /services/{serviceId}/tasks/{taskId}
  const taskMatch = pathname.match(/^\/services\/([^/]+)\/tasks\/([^/]+)$/);
  if (taskMatch) {
    return {
      currentView: 'services',
      selectedServiceId: decodeURIComponent(taskMatch[1]),
      activeTab: 'Tasks',
      selectedTaskId: decodeURIComponent(taskMatch[2]),
    };
  }

  // Service detail: /services/{serviceId}
  const serviceMatch = pathname.match(/^\/services\/([^/]+)$/);
  if (serviceMatch) {
    const serviceId = decodeURIComponent(serviceMatch[1]);
    const tab = paramToTab[(url.searchParams.get('tab') ?? '').toLowerCase()] ?? 'Tasks';
    return {
      currentView: 'services',
      selectedServiceId: serviceId,
      activeTab: tab,
      selectedTaskId: null,
    };
  }

  // Bare /services -> service list view
  if (pathname === '/services') {
    return { ...DEFAULT_ROUTE_STATE, currentView: 'servicesList' };
  }

  return DEFAULT_ROUTE_STATE;
}

export function createAppRoutePath(route: AppRouteState): string {
  if (route.currentView === 'overview') {
    return '/overview';
  }

  if (route.currentView === 'notifications') {
    return '/notifications';
  }

  // Service list view
  if (route.currentView === 'servicesList' || !route.selectedServiceId) {
    return '/services';
  }

  // Task detail within a service
  if (route.selectedTaskId) {
    return `/services/${encodeURIComponent(route.selectedServiceId)}/tasks/${encodeURIComponent(route.selectedTaskId)}`;
  }

  // Service detail (tabular)
  if (route.activeTab === 'Tasks') {
    return `/services/${encodeURIComponent(route.selectedServiceId)}`;
  }

  return `/services/${encodeURIComponent(route.selectedServiceId)}?tab=${tabToParam[route.activeTab]}`;
}
