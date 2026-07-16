export type ControlPlaneView = 'overview' | 'services' | 'notifications';
export type ServiceTab = 'Tasks' | 'Instances' | 'Configuration' | 'Logs';

export interface AppRouteState {
  currentView: ControlPlaneView;
  activeTab: ServiceTab;
  selectedTaskId: string | null;
}

const DEFAULT_ROUTE_STATE: AppRouteState = {
  currentView: 'services',
  activeTab: 'Tasks',
  selectedTaskId: null,
};

const tabToParam: Record<ServiceTab, string> = {
  Tasks: 'tasks',
  Instances: 'instances',
  Configuration: 'configuration',
  Logs: 'logs',
};

const paramToTab: Record<string, ServiceTab> = {
  tasks: 'Tasks',
  instances: 'Instances',
  configuration: 'Configuration',
  config: 'Configuration',
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

  const taskMatch = pathname.match(/^\/services\/tasks\/([^/]+)$/);
  if (taskMatch) {
    return {
      currentView: 'services',
      activeTab: 'Tasks',
      selectedTaskId: decodeURIComponent(taskMatch[1]),
    };
  }

  const tab = paramToTab[(url.searchParams.get('tab') ?? '').toLowerCase()] ?? DEFAULT_ROUTE_STATE.activeTab;
  return {
    currentView: 'services',
    activeTab: tab,
    selectedTaskId: null,
  };
}

export function createAppRoutePath(route: AppRouteState): string {
  if (route.currentView === 'overview') {
    return '/overview';
  }

  if (route.currentView === 'notifications') {
    return '/notifications';
  }

  if (route.selectedTaskId) {
    return `/services/tasks/${encodeURIComponent(route.selectedTaskId)}`;
  }

  if (route.activeTab === 'Tasks') {
    return '/services';
  }

  return `/services?tab=${tabToParam[route.activeTab]}`;
}
