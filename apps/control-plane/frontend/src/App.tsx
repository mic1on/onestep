import { useState, useEffect, useMemo, useRef, useCallback, type CSSProperties } from 'react';
import {
  dispatchInstanceCommand,
  dispatchServiceCommand,
  dispatchTaskManualRun,
  dispatchTaskCommand,
  formatRelativeTime,
  getFanoutCommandIds,
  getApiErrorMessage,
  getResourceCatalog,
  isAuthRequiredError,
  loadControlPlaneData,
  loadTaskManualRunTargets,
  loadTaskEventLogs,
  loadTaskMetricWindows,
  logoutConsole,
  parseManualRunPayload,
  pollTaskCommandCompletion,
  pollTaskPauseRequested,
  DEFAULT_TASK_EVENT_LOOKBACK_MINUTES,
  DEFAULT_TASK_EVENT_PAGE_SIZE,
  DEFAULT_TASK_METRIC_LOOKBACK_MINUTES,
  type Environment,
  type JsonObject,
  type ServiceCommandFanoutResponse,
  type ServiceSummaryStats,
  type ResourceCatalogEntry,
  type TaskManualRunTarget,
  type TaskMetricChartPointSummary,
} from './api';
import {
  INITIAL_SERVICES,
  INITIAL_TASKS,
  INITIAL_INSTANCES,
  INITIAL_LOGS,
} from './initialData';
import { Service, Task, Instance, LogEntry, type TaskCommandKind } from './types';
import Sidebar from './components/Sidebar';
import MobileNavigation from './components/MobileNavigation';
import ServicesList from './components/ServicesList';
import OverviewPage from './components/OverviewPage';
import TelemetryStats from './components/TelemetryStats';
import TasksList from './components/TasksList';
import InstancesTable from './components/InstancesTable';
import TopologyFlow from './components/TopologyFlow';
import ConfigEditor from './components/ConfigEditor';
import ResourceChart from './components/ResourceChart';
import TaskEventDiagnostics from './components/TaskEventDiagnostics';
import LoginPage from './components/LoginPage';
import NotificationSettingsPage from './components/NotificationSettingsPage';
import LocaleSwitcher from './components/LocaleSwitcher';
import ToastViewport, { type ToastMessage, type ToastType } from './components/ToastViewport';
import useDismissibleMenu from './components/useDismissibleMenu';
import {
  createAppRoutePath,
  parseAppRoute,
  type AppRouteState,
  type ControlPlaneView,
  type ServiceTab,
} from './appRoute';
import { useI18n } from './i18n';
import {
  Play,
  Pause,
  RotateCcw,
  RefreshCw,
  Workflow,
  ChevronRight,
  Terminal,
  ArrowLeft,
  Globe,
  Check,
  X,
  MoreHorizontal,
} from 'lucide-react';

const EMPTY_SERVICE: Service = {
  id: '',
  name: '',
  description: null,
  viewStatus: 'stopped',
  uptimeReferenceAt: null,
  throughputPerMin: 0,
  successRate: 0,
  errorCount: 0,
  totalInstances: 0,
  activeInstances: 0,
  standbyInstances: 0,
  totalTaskCount: 0,
  failingTaskCount: 0,
  onlineTaskCount: 0,
};

const EMPTY_SERVICE_SUMMARY: ServiceSummaryStats = {
  total_services: 0,
  online_services: 0,
  attention_services: 0,
  offline_services: 0,
  total_instances: 0,
  online_instances: 0,
  total_tasks: 0,
  failing_tasks: 0,
};

function taskSupportsCommand(task: Task | null | undefined, command: TaskCommandKind): boolean {
  return task?.supportedCommands.includes(command) ?? false;
}

export function getTaskToggleCommand(task: Task | null | undefined): 'pause_task' | 'resume_task' | null {
  if (task?.viewStatus === 'paused') return 'resume_task';
  if (task && task.viewStatus !== 'offline') return 'pause_task';
  return null;
}

export function isTaskToggleSupported(task: Task | null | undefined): boolean {
  const command = getTaskToggleCommand(task);
  return command !== null && taskSupportsCommand(task, command);
}

export function getControlPlaneLoadingMode(isLoading: boolean, serviceCount: number) {
  if (isLoading && serviceCount === 0) return 'initial' as const;
  if (isLoading) return 'refresh' as const;
  return 'settled' as const;
}

function ControlPlaneSkeleton({ label }: { label: string }) {
  return (
    <div
      aria-label={label}
      className="mx-auto max-w-7xl space-y-6"
      data-testid="control-plane-skeleton"
      role="status"
    >
      <h2 className="sr-only">{label}</h2>
      <div aria-hidden="true" className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {[0, 1, 2, 3].map((item) => (
          <div className="h-24 rounded-lg border border-slate-200 bg-white p-4" key={item}>
            <div className="ui-skeleton h-3 w-20 rounded" />
            <div className="ui-skeleton mt-5 h-7 w-14 rounded" />
          </div>
        ))}
      </div>
      <div aria-hidden="true" className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <div className="border-b border-slate-200 p-4">
          <div className="ui-skeleton h-8 w-64 max-w-full rounded" />
        </div>
        {[0, 1, 2, 3].map((item) => (
          <div
            className="grid grid-cols-[2fr_1fr_1fr] gap-4 border-b border-slate-100 p-4 last:border-0"
            key={item}
          >
            <div className="ui-skeleton h-4 rounded" />
            <div className="ui-skeleton h-4 rounded" />
            <div className="ui-skeleton h-4 rounded" />
          </div>
        ))}
      </div>
    </div>
  );
}

const SERVICE_TABS: ServiceTab[] = ['Tasks', 'Instances', 'Logs'];

export default function App() {
  const { t: tr } = useI18n();
  const initialRouteState = useMemo(() => parseAppRoute(`${window.location.pathname}${window.location.search}`), []);

  // --- API-backed state. Demo data is only used as a dev fallback after API failure. ---
  const [services, setServices] = useState<Service[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [instances, setInstances] = useState<Instance[]>([]);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [serviceSummary, setServiceSummary] = useState<ServiceSummaryStats>(EMPTY_SERVICE_SUMMARY);
  const [sourceKindCounts, setSourceKindCounts] = useState<Record<string, number>>({});
  const [resourceCatalog, setResourceCatalog] = useState<ResourceCatalogEntry[]>([]);
  const [apiConnected, setApiConnected] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [isLoadingApi, setIsLoadingApi] = useState(true);
  const [taskMetricWindows, setTaskMetricWindows] = useState<TaskMetricChartPointSummary[]>([]);
  const [taskMetricLookbackMinutes, setTaskMetricLookbackMinutes] = useState(DEFAULT_TASK_METRIC_LOOKBACK_MINUTES);
  const [taskMetricsError, setTaskMetricsError] = useState<string | null>(null);
  const [isLoadingTaskMetrics, setIsLoadingTaskMetrics] = useState(false);
  const [taskEventLogs, setTaskEventLogs] = useState<LogEntry[]>([]);
  const [taskEventLookbackMinutes, setTaskEventLookbackMinutes] = useState(DEFAULT_TASK_EVENT_LOOKBACK_MINUTES);
  const [taskEventOffset, setTaskEventOffset] = useState(0);
  const [taskEventTotal, setTaskEventTotal] = useState(0);
  const [taskEventsError, setTaskEventsError] = useState<string | null>(null);
  const [isLoadingTaskEvents, setIsLoadingTaskEvents] = useState(false);

  // --- UI Navigation State ---
  const [routePath, setRoutePath] = useState(() => `${window.location.pathname}${window.location.search}`);
  const [currentView, setCurrentView] = useState<ControlPlaneView>(initialRouteState.currentView);
  const [selectedServiceId, setSelectedServiceId] = useState<string>(initialRouteState.selectedServiceId ?? '');
  const [activeTab, setActiveTab] = useState<ServiceTab>(initialRouteState.activeTab);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(initialRouteState.selectedTaskId);

  // --- Global Environment Filter ---
  const [environmentFilter, setEnvironmentFilter] = useState<EnvironmentFilter>(() => readEnvironmentFilter());
  const [isEnvMenuOpen, setIsEnvMenuOpen] = useState(false);
  const closeEnvironmentMenu = useCallback(() => setIsEnvMenuOpen(false), []);
  const { menuRef: environmentMenuRef, triggerRef: environmentMenuTriggerRef } = useDismissibleMenu({
    onClose: closeEnvironmentMenu,
    open: isEnvMenuOpen,
  });
  const [isTaskActionMenuOpen, setIsTaskActionMenuOpen] = useState(false);
  const closeTaskActionMenu = useCallback(() => setIsTaskActionMenuOpen(false), []);
  const { menuRef: taskActionMenuRef, triggerRef: taskActionMenuTriggerRef } = useDismissibleMenu({
    onClose: closeTaskActionMenu,
    open: isTaskActionMenuOpen,
  });
  // Tracks the previously seen environment filter so the reset effect below only
  // fires when the user *changes* the filter — not on initial mount. Without this
  // guard, refreshing a deep-linked service/task detail page (where
  // currentView === 'services') would redirect back to /services.
  const prevEnvironmentFilterRef = useRef<EnvironmentFilter>(environmentFilter);
  const refreshRequestSeqRef = useRef(0);

  // --- Loading / Overlay State ---
  const [isRestartingAll, setIsRestartingAll] = useState(false);
  const [isDeploying, setIsDeploying] = useState(false);
  const [isLogoutPending, setIsLogoutPending] = useState(false);
  const [deploymentProgress, setDeploymentProgress] = useState(0);
  const [refreshFeedbackKey, setRefreshFeedbackKey] = useState(0);
  // Task id currently awaiting a pause/resume outcome from the worker (command
  // dispatched, polling pause_requested until it reflects the new state or times
  // out). Drives the Loading state on the toggle button for that task.
  const [pendingTaskId, setPendingTaskId] = useState<string | null>(null);
  const [manualRunTaskId, setManualRunTaskId] = useState<string | null>(null);
  const [manualRunTargets, setManualRunTargets] = useState<TaskManualRunTarget[]>([]);
  const [manualRunTargetIds, setManualRunTargetIds] = useState<string[]>([]);
  const [manualRunPayloadText, setManualRunPayloadText] = useState('{}');
  const [manualRunReason, setManualRunReason] = useState('');
  const [manualRunError, setManualRunError] = useState<string | null>(null);
  const [isManualRunLoadingTargets, setIsManualRunLoadingTargets] = useState(false);
  const [isManualRunSubmitting, setIsManualRunSubmitting] = useState(false);
  const manualRunDialogRef = useRef<HTMLDivElement>(null);
  const manualRunReturnFocusRef = useRef<HTMLElement | null>(null);

  // --- Toast Notifications ---
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  useEffect(() => {
    const handlePopState = () => setRoutePath(`${window.location.pathname}${window.location.search}`);
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  const replaceRoute = useCallback((nextPath: string) => {
    window.history.replaceState(null, '', nextPath);
    setRoutePath(`${window.location.pathname}${window.location.search}`);
  }, []);

  const pushRoute = useCallback((nextPath: string) => {
    const currentPath = `${window.location.pathname}${window.location.search}`;
    if (currentPath !== nextPath) {
      window.history.pushState(null, '', nextPath);
    }
    setRoutePath(`${window.location.pathname}${window.location.search}`);
  }, []);

  const applyRouteState = useCallback((nextRoute: AppRouteState) => {
    setCurrentView(nextRoute.currentView);
    setActiveTab(nextRoute.activeTab);
    setSelectedTaskId(nextRoute.selectedTaskId);
    if (nextRoute.selectedServiceId) {
      setSelectedServiceId(nextRoute.selectedServiceId);
    }
  }, []);

  const navigateToRoute = useCallback(
    (nextRoute: AppRouteState) => {
      applyRouteState(nextRoute);
      pushRoute(createAppRoutePath(nextRoute));
    },
    [applyRouteState, pushRoute],
  );

  const navigateToView = useCallback(
    (view: ControlPlaneView) => {
      navigateToRoute({ currentView: view, selectedServiceId: null, activeTab: 'Tasks', selectedTaskId: null });
    },
    [navigateToRoute],
  );

  const navigateToServicesList = useCallback(() => {
    navigateToRoute({
      currentView: 'servicesList',
      selectedServiceId: null,
      activeTab: 'Tasks',
      selectedTaskId: null,
    });
  }, [navigateToRoute]);

  const navigateToService = useCallback(
    (serviceId: string) => {
      navigateToRoute({
        currentView: 'services',
        selectedServiceId: serviceId,
        activeTab: 'Tasks',
        selectedTaskId: null,
      });
    },
    [navigateToRoute],
  );

  const navigateToServiceTab = useCallback(
    (tab: ServiceTab) => {
      navigateToRoute({
        currentView: 'services',
        selectedServiceId: selectedServiceId,
        activeTab: tab,
        selectedTaskId: null,
      });
    },
    [navigateToRoute, selectedServiceId],
  );

  const navigateToTask = useCallback(
    (taskId: string) => {
      navigateToRoute({
        currentView: 'services',
        selectedServiceId: selectedServiceId,
        activeTab: 'Tasks',
        selectedTaskId: taskId,
      });
    },
    [navigateToRoute, selectedServiceId],
  );

  const redirectToLogin = useCallback(() => {
    const currentPath = `${window.location.pathname}${window.location.search}`;
    const nextPath = currentPath.startsWith('/login') ? '/' : currentPath;
    replaceRoute(`/login?next=${encodeURIComponent(nextPath)}`);
  }, [replaceRoute]);

  const dismissToast = useCallback((id: string) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const addToast = (message: string, type: ToastType = 'success') => {
    const id = Math.random().toString(36).slice(2, 11);
    setToasts((current) => [...current, { id, message, type }]);
  };

  const refreshControlPlaneData = async (
    targetServiceId = selectedServiceId,
    silent = false,
    targetEnvironment: EnvironmentFilter = environmentFilter,
  ) => {
    const requestSeq = refreshRequestSeqRef.current + 1;
    refreshRequestSeqRef.current = requestSeq;
    const isLatestRequest = () => refreshRequestSeqRef.current === requestSeq;

    setIsLoadingApi(true);
    try {
      const [data, catalog] = await Promise.all([
        loadControlPlaneData(targetServiceId, apiEnvironment(targetEnvironment)),
        getResourceCatalog(),
      ]);
      if (!isLatestRequest()) {
        return;
      }
      setServices(data.services);
      setTasks(data.tasks);
      setInstances(data.instances);
      setLogs(data.logs.length > 0 ? data.logs : []);
      setServiceSummary(data.serviceSummary);
      setSourceKindCounts(data.sourceKindCounts);
      setResourceCatalog(catalog.resources);
      setApiConnected(true);
      setApiError(null);
      if (data.selectedServiceId !== targetServiceId) {
        setSelectedServiceId(data.selectedServiceId);
      }
      if (!silent) {
        setRefreshFeedbackKey((current) => current + 1);
        addToast(tr('toast.controlPlaneRefreshed'), 'success');
      }
    } catch (error) {
      if (!isLatestRequest()) {
        return;
      }
      if (isAuthRequiredError(error)) {
        setApiConnected(false);
        setApiError(null);
        redirectToLogin();
        return;
      }
      const message = getApiErrorMessage(error);
      setApiConnected(false);
      setApiError(message);
      if (import.meta.env.DEV && services.length === 0) {
        setServices(INITIAL_SERVICES);
        setTasks(INITIAL_TASKS);
        setInstances(INITIAL_INSTANCES);
        setLogs(INITIAL_LOGS);
        setResourceCatalog([]);
        setSelectedServiceId(INITIAL_SERVICES[0]?.id ?? '');
      }
      if (!silent) {
        addToast(tr('toast.apiFailed', { message }), 'warn');
      }
    } finally {
      if (isLatestRequest()) {
        setIsLoadingApi(false);
      }
    }
  };

  const handleApiActionError = (error: unknown, fallbackMessage: string) => {
    if (isAuthRequiredError(error)) {
      redirectToLogin();
      return;
    }
    addToast(tr('toast.withError', { action: fallbackMessage, message: getApiErrorMessage(error) }), 'warn');
  };

  const handleLogout = async () => {
    setIsLogoutPending(true);
    try {
      await logoutConsole();
      replaceRoute('/login');
    } catch (error) {
      if (isAuthRequiredError(error)) {
        replaceRoute('/login');
        return;
      }
      addToast(tr('toast.logoutFailed', { message: getApiErrorMessage(error) }), 'warn');
    } finally {
      setIsLogoutPending(false);
    }
  };

  const handleFanoutResponse = (response: ServiceCommandFanoutResponse, targetName: string, acceptedMessage: string) => {
    const acceptedCount = (response.counts.dispatched ?? 0) + (response.counts.queued ?? 0);
    if (acceptedCount > 0) {
      addToast(acceptedMessage, 'success');
      return true;
    }

    const toastKey =
      response.noop_reason_code === 'no_online_instances' || response.counts.total === 0
        ? 'toast.serviceNoOnlineInstances'
        : 'toast.serviceCommandNoTargets';
    addToast(tr(toastKey, { name: targetName }), 'warn');
    return false;
  };

  useEffect(() => {
    if (routePath.startsWith('/login')) return;
    applyRouteState(parseAppRoute(routePath));
  }, [applyRouteState, routePath]);

  useEffect(() => {
    if (routePath.startsWith('/login')) return;
    void refreshControlPlaneData(selectedServiceId, true);
  }, [routePath]);

  useEffect(() => {
    if (apiConnected && selectedServiceId) {
      void refreshControlPlaneData(selectedServiceId, true);
    }
  }, [selectedServiceId]);

  useEffect(() => {
    if (routePath.startsWith('/login')) return;
    // Only react to *user-initiated* environment changes, not the initial mount.
    // On mount `prevEnvironmentFilterRef.current` equals `environmentFilter`.
    // The route effect above already loaded data for the parsed service id, so
    // doing another unscoped refresh here would overwrite deep-linked details
    // with the first service in the list.
    const hasChanged = prevEnvironmentFilterRef.current !== environmentFilter;
    prevEnvironmentFilterRef.current = environmentFilter;
    if (!hasChanged) {
      return;
    }
    // Changing the environment filter invalidates the current service/task context.
    // Return to the services list so the user sees the filtered set.
    if (currentView === 'services') {
      navigateToServicesList();
      return;
    }
    void refreshControlPlaneData(undefined, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [environmentFilter]);

  // --- Active Objects Selectors ---
  const selectedService = useMemo(() => {
    return services.find((s) => s.id === selectedServiceId) || services[0] || EMPTY_SERVICE;
  }, [services, selectedServiceId]);

  const activeServiceTasks = useMemo(() => {
    return tasks.filter((t) => t.serviceId === selectedServiceId);
  }, [tasks, selectedServiceId]);

  const activeServiceInstances = useMemo(() => {
    return instances.filter((i) => i.serviceId === selectedServiceId);
  }, [instances, selectedServiceId]);

  const selectedTask = useMemo(() => {
    if (!selectedTaskId) return null;
    return tasks.find((t) => t.id === selectedTaskId) || null;
  }, [tasks, selectedTaskId]);
  const manualRunTask = useMemo(() => {
    if (!manualRunTaskId) return null;
    return tasks.find((t) => t.id === manualRunTaskId) || null;
  }, [manualRunTaskId, tasks]);
  const serviceHasOnlineInstances = selectedService.activeInstances > 0;
  const selectedTaskIsOffline = selectedTask?.viewStatus === 'offline' || !serviceHasOnlineInstances;
  const selectedTaskToggleCommand = getTaskToggleCommand(selectedTask);
  const selectedTaskToggleIsPause = selectedTaskToggleCommand === 'pause_task';
  const selectedTaskCanToggle = isTaskToggleSupported(selectedTask);
  const selectedTaskCanRestart = taskSupportsCommand(selectedTask, 'restart_task');
  const selectedTaskCanRunOnce = taskSupportsCommand(selectedTask, 'run_task_once');
  const isPendingTaskToggle = !!selectedTask && pendingTaskId === selectedTask.id;
  const headerStatus = getHeaderStatus(selectedService.viewStatus, selectedTask?.viewStatus, tr);
  const loadingMode = getControlPlaneLoadingMode(isLoadingApi, services.length);
  const handleTaskEventLookbackMinutesChange = useCallback((minutes: number) => {
    setTaskEventLookbackMinutes(minutes);
    setTaskEventOffset(0);
  }, []);

  // --- Live Terminal Logs Simulator ---
  const logTerminalRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (logTerminalRef.current) {
      logTerminalRef.current.scrollTop = logTerminalRef.current.scrollHeight;
    }
  }, [logs]);

  useEffect(() => {
    setTaskEventOffset(0);
  }, [selectedTaskId]);

  useEffect(() => {
    let cancelled = false;

    if (!selectedTask) {
      setTaskMetricWindows([]);
      setTaskMetricsError(null);
      setIsLoadingTaskMetrics(false);
      return () => {
        cancelled = true;
      };
    }

    setIsLoadingTaskMetrics(true);
    setTaskMetricsError(null);
    void loadTaskMetricWindows(selectedTask, taskMetricLookbackMinutes)
      .then((windows) => {
        if (cancelled) return;
        setTaskMetricWindows(windows);
      })
      .catch((error) => {
        if (cancelled) return;
        if (isAuthRequiredError(error)) {
          redirectToLogin();
          return;
        }
        setTaskMetricWindows([]);
        setTaskMetricsError(getApiErrorMessage(error));
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoadingTaskMetrics(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [redirectToLogin, selectedTask, taskMetricLookbackMinutes]);

  useEffect(() => {
    let cancelled = false;

    if (!selectedTask) {
      setTaskEventLogs([]);
      setTaskEventTotal(0);
      setTaskEventsError(null);
      setIsLoadingTaskEvents(false);
      return () => {
        cancelled = true;
      };
    }

    setIsLoadingTaskEvents(true);
    setTaskEventsError(null);
    setTaskEventLogs([]);
    void loadTaskEventLogs(selectedTask, {
      lookbackMinutes: taskEventLookbackMinutes,
      limit: DEFAULT_TASK_EVENT_PAGE_SIZE,
      offset: taskEventOffset,
    })
      .then((page) => {
        if (cancelled) return;
        setTaskEventLogs(page.logs);
        setTaskEventTotal(page.total);
      })
      .catch((error) => {
        if (cancelled) return;
        if (isAuthRequiredError(error)) {
          redirectToLogin();
          return;
        }
        setTaskEventLogs([]);
        setTaskEventTotal(0);
        setTaskEventsError(getApiErrorMessage(error));
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoadingTaskEvents(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [redirectToLogin, selectedTask, taskEventLookbackMinutes, taskEventOffset]);

  // --- Interactive Control plane Handlers ---

  // Restart All Service Components
  const handleRestartAll = async () => {
    if (selectedService.apiName && selectedService.environment) {
      if (!serviceHasOnlineInstances) {
        addToast(tr('toast.serviceNoOnlineInstances', { name: selectedService.name }), 'warn');
        return;
      }
      setIsRestartingAll(true);
      addToast(tr('toast.restartAllDispatch', { name: selectedService.name }), 'info');
      try {
        const response = await dispatchServiceCommand(selectedService, 'restart');
        handleFanoutResponse(response, selectedService.name, tr('toast.restartAccepted', { name: selectedService.name }));
        await refreshControlPlaneData(selectedService.id, true);
      } catch (error) {
        handleApiActionError(error, tr('error.restartFailed'));
      } finally {
        setIsRestartingAll(false);
      }
      return;
    }

    // No connected control plane: do not simulate. The button is disabled in
    // this state (see the action trigger markup), so reaching here means the
    // service has no API identity yet — surface that honestly instead of
    // faking a restart via setState.
    addToast(tr('toast.serviceCommandNoTargets', { name: selectedService.name }), 'warn');
  };

  // Deploy Config / Code Update Simulation
  const handleDeployUpdate = async () => {
    if (selectedService.apiName && selectedService.environment) {
      if (!serviceHasOnlineInstances) {
        addToast(tr('toast.serviceNoOnlineInstances', { name: selectedService.name }), 'warn');
        return;
      }
      setIsDeploying(true);
      setDeploymentProgress(30);
      addToast(tr('toast.deployDispatch', { name: selectedService.name }), 'info');
      try {
        const response = await dispatchServiceCommand(selectedService, 'sync_now');
        setDeploymentProgress(100);
        handleFanoutResponse(response, selectedService.name, tr('toast.deployAccepted', { name: selectedService.name }));
        await refreshControlPlaneData(selectedService.id, true);
      } catch (error) {
        handleApiActionError(error, tr('error.syncFailed'));
      } finally {
        setTimeout(() => {
          setIsDeploying(false);
          setDeploymentProgress(0);
        }, 600);
      }
      return;
    }

    // No connected control plane: do not simulate a rolling deploy. The button
    // is disabled in this state; reaching here means no API identity, so
    // surface that honestly instead of faking deploy progress + version bumps.
    addToast(tr('toast.serviceCommandNoTargets', { name: selectedService.name }), 'warn');
  };

  // Toggle Task (Running <-> Paused/Stopped). Dispatches pause/resume, then
  // polls pause_requested until the worker reflects the new state (or times out)
  // before updating the UI, so the button flips only once the action is
  // confirmed. The worker reports pause_requested via heartbeat (up to ~30s
  // latency), not the command ack, hence the explicit poll.
  const handleToggleTaskStatus = async (taskId: string) => {
    const task = tasks.find((item) => item.id === taskId);
    if (task?.apiName) {
      const taskService = services.find((service) => service.id === task.serviceId);
      if (task.viewStatus === 'offline' || taskService?.activeInstances === 0) {
        addToast(tr('toast.serviceNoOnlineInstances', { name: taskService?.name ?? task.name }), 'warn');
        return;
      }
      const kind = getTaskToggleCommand(task);
      if (kind === null) {
        addToast(tr('toast.taskToggleUnavailable', { name: task.name }), 'warn');
        return;
      }
      if (!taskSupportsCommand(task, kind)) {
        addToast(tr('toast.taskCommandUnsupported', { name: task.name }), 'warn');
        return;
      }
      const expectedPause = kind === 'pause_task'; // pause_task -> wait for pause_requested=true
      addToast(tr('toast.taskDispatch', { kind: kind.replace('_', ' '), name: task.name }), 'info');
      setPendingTaskId(taskId);
      try {
        const response = await dispatchTaskCommand(task, kind);
        const accepted = handleFanoutResponse(response, task.name, tr('toast.commandAccepted', { name: task.name }));
        if (!accepted) {
          // No eligible targets (skipped/rejected); nothing to wait for.
          return;
        }
        // Synchronously wait for the worker to report the new pause state.
        const confirmed = await pollTaskPauseRequested(task, expectedPause);
        // Refresh regardless so the list/detail reflect whatever state landed.
        await refreshControlPlaneData(task.serviceId, true);
        if (!confirmed) {
          // Command was dispatched but the worker heartbeat hasn't reflected it
          // within the timeout. Do not roll back — the action is still in effect.
          addToast(tr('toast.taskPauseTimeout', { name: task.name }), 'warn');
        }
      } catch (error) {
        handleApiActionError(error, tr('error.taskCommandFailed'));
      } finally {
        setPendingTaskId((current) => (current === taskId ? null : current));
      }
      return;
    }

    // No connected control plane: button is disabled in this state. Do not
    // fake a status flip via setState.
    addToast(tr('toast.serviceCommandNoTargets', { name: task?.name ?? taskId }), 'warn');
  };

  // Restart Specific Task. Dispatches the single `restart_task` command, which
  // the worker handles by cancelling the task's runner, closing/reopening its
  // private source, and spawning a fresh runner (true per-task restart without
  // restarting the whole process). This replaces the old pause_task + resume_task
  // sequence, which raced on the worker (resume cleared the pause flag before
  // the runner ever parked) and never actually restarted anything.
  const handleRestartTask = async (taskId: string) => {
    const task = tasks.find((item) => item.id === taskId);
    if (task?.apiName) {
      const taskService = services.find((service) => service.id === task.serviceId);
      if (task.viewStatus === 'offline' || taskService?.activeInstances === 0) {
        addToast(tr('toast.serviceNoOnlineInstances', { name: taskService?.name ?? task.name }), 'warn');
        return;
      }
      if (!taskSupportsCommand(task, 'restart_task')) {
        addToast(tr('toast.taskCommandUnsupported', { name: task.name }), 'warn');
        return;
      }
      addToast(tr('toast.taskRestartDispatch', { name: task.name }), 'info');
      setPendingTaskId(taskId);
      try {
        const response = await dispatchTaskCommand(task, 'restart_task');
        const accepted = handleFanoutResponse(
          response,
          task.name,
          tr('toast.taskRestartAccepted', { name: task.name }),
        );
        if (!accepted) {
          // No eligible targets (older worker without command.restart_task, or
          // no online instances); handleFanoutResponse already toasted.
          await refreshControlPlaneData(task.serviceId, true);
          return;
        }
        const completion = await pollTaskCommandCompletion(task, getFanoutCommandIds(response));
        // Refresh regardless so the detail/list reflect whatever landed. The
        // fresh runner reports a new metric window via the next heartbeat.
        await refreshControlPlaneData(task.serviceId, true);
        if (completion.failed) {
          addToast(tr('toast.taskRestartFailed', { name: task.name }), 'warn');
        } else if (!completion.completed) {
          addToast(tr('toast.taskRestartTimeout', { name: task.name }), 'warn');
        }
      } catch (error) {
        handleApiActionError(error, tr('error.taskRestartFailed'));
      } finally {
        setPendingTaskId((current) => (current === taskId ? null : current));
      }
      return;
    }

    // No connected control plane: button is disabled in this state. Do not
    // fake a restart via setState.
    addToast(tr('toast.serviceCommandNoTargets', { name: task?.name ?? taskId }), 'warn');
  };

  const closeManualRunDialog = useCallback((force = false) => {
    if (isManualRunSubmitting && !force) return;
    setManualRunTaskId(null);
    setManualRunTargets([]);
    setManualRunTargetIds([]);
    setManualRunPayloadText('{}');
    setManualRunReason('');
    setManualRunError(null);
    setIsManualRunLoadingTargets(false);
    window.setTimeout(() => manualRunReturnFocusRef.current?.focus(), 0);
  }, [isManualRunSubmitting]);

  useEffect(() => {
    const dialog = manualRunDialogRef.current;
    if (!manualRunTask || !dialog) return;

    const focusableSelector = [
      'button:not(:disabled)',
      'input:not(:disabled)',
      'textarea:not(:disabled)',
      '[tabindex]:not([tabindex="-1"])',
    ].join(',');
    const initialFocus = dialog.querySelector<HTMLElement>('[data-autofocus="true"]');
    initialFocus?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !isManualRunSubmitting) {
        event.preventDefault();
        closeManualRunDialog();
        return;
      }
      if (event.key !== 'Tab') return;

      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(focusableSelector));
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;

      if (event.shiftKey && (active === first || !dialog.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [closeManualRunDialog, isManualRunSubmitting, manualRunTask]);

  const handleOpenManualRun = async (taskId: string) => {
    const task = tasks.find((item) => item.id === taskId);
    if (!task?.apiName) {
      addToast(tr('toast.serviceCommandNoTargets', { name: task?.name ?? taskId }), 'warn');
      return;
    }
    if (!apiConnected) {
      addToast(tr('toast.serviceCommandNoTargets', { name: task.name }), 'warn');
      return;
    }
    if (!taskSupportsCommand(task, 'run_task_once')) {
      addToast(tr('toast.taskCommandUnsupported', { name: task.name }), 'warn');
      return;
    }

    manualRunReturnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setManualRunTaskId(task.id);
    setManualRunTargets([]);
    setManualRunTargetIds([]);
    setManualRunPayloadText('{}');
    setManualRunReason('');
    setManualRunError(null);
    setIsManualRunLoadingTargets(true);
    addToast(tr('toast.taskManualRunDispatch', { name: task.name }), 'info');

    try {
      const targets = await loadTaskManualRunTargets(task);
      setManualRunTargets(targets);
      setManualRunTargetIds(targets.map((target) => target.instanceId));
    } catch (error) {
      if (isAuthRequiredError(error)) {
        redirectToLogin();
        return;
      }
      setManualRunError(getApiErrorMessage(error));
    } finally {
      setIsManualRunLoadingTargets(false);
    }
  };

  const toggleManualRunTarget = (instanceId: string) => {
    setManualRunTargetIds((current) =>
      current.includes(instanceId)
        ? current.filter((targetId) => targetId !== instanceId)
        : [...current, instanceId],
    );
  };

  const handleManualRunSubmit = async () => {
    if (!manualRunTask) return;
    setManualRunError(null);

    if (manualRunTargetIds.length === 0) {
      setManualRunError(tr('manualRun.selectTargetError'));
      return;
    }
    const trimmedReason = manualRunReason.trim();
    if (!trimmedReason) {
      setManualRunError(tr('manualRun.reasonRequired'));
      return;
    }

    let payload: JsonObject;
    try {
      payload = parseManualRunPayload(manualRunPayloadText);
    } catch {
      setManualRunError(tr('manualRun.payloadObjectError'));
      return;
    }

    setIsManualRunSubmitting(true);
    try {
      const response = await dispatchTaskManualRun(manualRunTask, {
        targetInstanceIds: manualRunTargetIds,
        payload,
        reason: trimmedReason,
      });
      const accepted = handleFanoutResponse(
        response,
        manualRunTask.name,
        tr('toast.taskManualRunAccepted', { name: manualRunTask.name }),
      );
      if (accepted) {
        closeManualRunDialog(true);
        await refreshControlPlaneData(manualRunTask.serviceId, true);
      }
    } catch (error) {
      handleApiActionError(error, tr('error.taskManualRunFailed'));
    } finally {
      setIsManualRunSubmitting(false);
    }
  };

  // Restart Instance
  const handleRestartInstance = async (uuid: string) => {
    const instance = instances.find((item) => item.uuid === uuid);
    if (instance?.apiServiceName) {
      setInstances((prev) =>
        prev.map((item) => (item.uuid === uuid ? { ...item, viewStatus: 'starting' } : item))
      );
      addToast(tr('toast.instanceRestartDispatch', { id: uuid.slice(0, 6) }), 'info');
      try {
        await dispatchInstanceCommand(instance, 'restart');
        addToast(tr('toast.instanceRestartAccepted', { id: uuid.slice(0, 6) }), 'success');
        await refreshControlPlaneData(instance.serviceId, true);
      } catch (error) {
        handleApiActionError(error, tr('error.instanceRestartFailed'));
      }
      return;
    }

    // No connected control plane: button is disabled in this state. Do not
    // fake a restart via setState.
    addToast(tr('toast.serviceCommandNoTargets', { name: uuid.slice(0, 8) }), 'warn');
  };

  // Toggle Instance (Stop / Start)
  const handleToggleInstance = async (uuid: string) => {
    const instance = instances.find((item) => item.uuid === uuid);
    if (instance?.apiServiceName) {
      if (instance.viewStatus !== 'running') {
        addToast(tr('toast.instanceStartUnavailable'), 'warn');
        return;
      }
      addToast(tr('toast.instanceShutdownDispatch', { id: uuid.slice(0, 6) }), 'info');
      try {
        await dispatchInstanceCommand(instance, 'shutdown');
        addToast(tr('toast.instanceShutdownAccepted', { id: uuid.slice(0, 6) }), 'success');
        await refreshControlPlaneData(instance.serviceId, true);
      } catch (error) {
        handleApiActionError(error, tr('error.instanceShutdownFailed'));
      }
      return;
    }

    // No connected control plane: button is disabled in this state. Do not
    // fake a stop/start flip via setState (and do not fake starting an
    // offline instance, which the runtime cannot do remotely).
    addToast(tr('toast.serviceCommandNoTargets', { name: uuid.slice(0, 8) }), 'warn');
  };

  if (routePath.startsWith('/login')) {
    return (
      <LoginPage
        onAuthenticated={(nextPath) => {
          replaceRoute(nextPath);
        }}
      />
    );
  }

  return (
    <div className="flex min-h-[100dvh] overflow-x-hidden bg-slate-50 font-sans text-slate-800">
      {/* --- Sidebar Navigator panel --- */}
      <Sidebar
        currentView={currentView}
        isLogoutPending={isLogoutPending}
        onLogout={() => void handleLogout()}
        onViewChange={navigateToView}
      />
      <MobileNavigation
        currentView={currentView}
        isLogoutPending={isLogoutPending}
        onLogout={() => void handleLogout()}
        onViewChange={navigateToView}
      />

      {/* --- Main Content Panel --- */}
      <div className="flex h-[100dvh] min-w-0 flex-1 flex-col overflow-hidden lg:ml-[240px]">
        {/* --- Core Content Stage Canvas --- */}
        <main className="flex-1 overflow-y-auto bg-slate-50 p-3 pb-[calc(5.5rem+env(safe-area-inset-bottom))] sm:p-5 sm:pb-[calc(5.5rem+env(safe-area-inset-bottom))] lg:p-6 lg:pb-6">
          <div
            data-testid="global-context-bar"
            className="mx-auto mb-3 flex max-w-7xl flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-600 shadow-xs sm:mb-4 sm:px-4"
          >
            <div className="flex min-w-0 flex-wrap items-center gap-2 sm:gap-3">
              <div className="flex items-center gap-2">
                <span
                  className={`h-2 w-2 rounded-full ${
                    apiConnected ? 'bg-emerald-500' : apiError ? 'bg-amber-500' : 'bg-slate-300'
                  }`}
                />
                <span>
                  {apiConnected
                    ? tr('api.connected')
                    : apiError
                    ? tr('api.usingDemoData', { error: apiError })
                    : tr('api.connecting')}
                </span>
              </div>

              <div className="h-3.5 w-px bg-slate-200" />

              {/* Global Environment Filter */}
              <div className="relative">
                <button
                  ref={environmentMenuTriggerRef}
                  onClick={() => setIsEnvMenuOpen((open) => !open)}
                  className="ui-pressable flex items-center gap-1.5 rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] font-bold text-slate-700 hover:bg-slate-100"
                  aria-haspopup="listbox"
                  aria-expanded={isEnvMenuOpen}
                  type="button"
                >
                  <Globe className="h-3 w-3 text-indigo-600" />
                  <span>{tr('top.environment')}</span>
                  <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-indigo-700">
                    {environmentFilterLabel(environmentFilter, tr)}
                  </span>
                </button>
                {isEnvMenuOpen && (
                    <ul
                      ref={environmentMenuRef}
                      role="listbox"
                      className="ui-popover-enter absolute right-0 z-40 mt-1 w-44 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 text-xs font-medium shadow-lg"
                    >
                      {ENVIRONMENT_FILTER_ORDER.map((value) => {
                        const active = value === environmentFilter;
                        return (
                          <li key={value}>
                            <button
                              role="option"
                              aria-selected={active}
                              onClick={() => {
                                setEnvironmentFilter(value);
                                writeEnvironmentFilter(value);
                                closeEnvironmentMenu();
                                environmentMenuTriggerRef.current?.focus();
                              }}
                              className={`ui-pressable flex w-full items-center justify-between px-3 py-1.5 text-left ${
                                active
                                  ? 'bg-indigo-50 font-bold text-indigo-700'
                                  : 'text-slate-700 hover:bg-slate-50'
                              }`}
                            >
                              <span>{environmentFilterLabel(value, tr)}</span>
                              {active && <Check className="h-3 w-3 text-indigo-600" />}
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                )}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <div className="hidden lg:block"><LocaleSwitcher /></div>
              <button
                aria-busy={loadingMode === 'refresh'}
                onClick={() => void refreshControlPlaneData(selectedServiceId)}
                disabled={isLoadingApi}
                aria-label={isLoadingApi ? tr('button.refreshing') : tr('button.refresh')}
                className="ui-pressable grid h-11 w-11 place-items-center rounded-md border border-slate-200 bg-slate-50 text-slate-600 hover:bg-slate-100 disabled:cursor-wait disabled:opacity-60 lg:flex lg:h-auto lg:w-auto lg:min-w-[92px] lg:gap-1 lg:px-2 lg:py-1"
                type="button"
              >
                <RefreshCw className={`h-4 w-4 lg:h-3 lg:w-3 ${isLoadingApi ? 'animate-spin' : ''}`} />
                <span className="hidden lg:inline">{isLoadingApi ? tr('button.refreshing') : tr('button.refresh')}</span>
              </button>
            </div>
          </div>

          {loadingMode === 'initial' ? (
            <ControlPlaneSkeleton label={tr('api.connecting')} />
          ) : services.length === 0 ? (
            <div className="mx-auto max-w-7xl rounded-lg border border-slate-200 bg-white p-8 text-center shadow-xs">
              <h2 className="text-sm font-bold text-slate-800">{tr('api.noServices')}</h2>
              <p className="mt-1 text-xs font-medium text-slate-500">
                {apiError ? tr('api.noServicesHint', { error: apiError }) : tr('api.waitingForServices')}
              </p>
            </div>
          ) : (
            <div className="relative">
          {refreshFeedbackKey > 0 && <span className="ui-refresh-ack" key={refreshFeedbackKey} />}
          {/* --- VIEW: Global Overview (if selected) --- */}
          {currentView === 'overview' && (
            <OverviewPage
              services={services}
              serviceSummary={serviceSummary}
              sourceKindCounts={sourceKindCounts}
              environment={apiEnvironment(environmentFilter)}
              onSelectService={navigateToService}
              onAuthRequired={redirectToLogin}
            />
          )}

          {currentView === 'notifications' && (
            <NotificationSettingsPage
              onAuthRequired={redirectToLogin}
              onNotify={addToast}
            />
          )}

          {/* --- VIEW: Services List --- */}
          {currentView === 'servicesList' && (
            <ServicesList services={services} onSelectService={navigateToService} />
          )}

          {/* --- VIEW: Service Detail --- */}
          {currentView === 'services' && (
            <div
              className="ui-page-enter mx-auto max-w-7xl space-y-6"
              key={`${selectedServiceId}:${activeTab}:${selectedTaskId ?? 'service'}`}
            >
              {/* Breadcrumb navigator under Command Bar */}
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <nav aria-label="Breadcrumb" className="flex w-full min-w-0 items-center overflow-hidden whitespace-nowrap text-slate-400 font-medium text-xs mb-1">
                    <button
                      type="button"
                      onClick={navigateToServicesList}
                      className="shrink-0 rounded-sm hover:text-indigo-600 focus:outline-hidden focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 transition-colors"
                    >
                      {tr('nav.services')}
                    </button>
                    <ChevronRight className="w-3.5 h-3.5 mx-1 shrink-0" />
                    {selectedTask ? (
                      <button
                        type="button"
                        onClick={() => navigateToService(selectedServiceId)}
                        title={selectedService.name}
                        className="min-w-0 max-w-[45%] truncate rounded-sm text-slate-800 font-bold hover:text-indigo-600 focus:outline-hidden focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 transition-colors sm:max-w-none"
                      >
                        {selectedService.name}
                      </button>
                    ) : (
                      <span className="flex min-w-0 items-center gap-2">
                        <span data-testid="service-breadcrumb-name" className="min-w-0 truncate text-slate-800 font-bold">
                          {selectedService.name}
                        </span>
                        <span data-testid="mobile-service-status" title={headerStatus.label} className="inline-flex shrink-0 sm:hidden">
                          <span aria-hidden="true" className={`h-2 w-2 rounded-full ${headerStatus.dotClassName}`} />
                          <span className="sr-only">{headerStatus.label}</span>
                        </span>
                      </span>
                    )}
                    {selectedTask && (
                      <>
                        <ChevronRight className="w-3.5 h-3.5 mx-1 shrink-0" />
                        <button
                          type="button"
                          onClick={() => navigateToServiceTab('Tasks')}
                          className="shrink-0 rounded-sm text-slate-500 font-semibold hover:text-indigo-600 focus:outline-hidden focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 transition-colors"
                        >
                          {tr('tabs.tasks')}
                        </button>
                        <ChevronRight className="w-3.5 h-3.5 mx-1 shrink-0" />
                        <span title={selectedTask.name} className="min-w-0 truncate text-slate-900 font-bold">
                          {selectedTask.name}
                        </span>
                      </>
                    )}
                  </nav>

                  <div className="flex items-center gap-3 mt-1.5">
                    <button
                      onClick={navigateToServicesList}
                      className="ui-pressable rounded-md p-1.5 text-slate-400 hover:bg-slate-100 hover:text-indigo-600"
                      title={tr('nav.services')}
                      aria-label={tr('nav.services')}
                      type="button"
                    >
                      <ArrowLeft className="w-4 h-4" />
                    </button>
                    <h2 className="min-w-0 break-words text-2xl font-extrabold text-slate-900 tracking-tight font-sans">
                      {selectedTask ? selectedTask.name : selectedService.name}
                    </h2>
                    <span
                      data-testid="service-header-status"
                      className={`${headerStatus.className} ${selectedTask ? 'flex' : 'hidden sm:flex'} items-center gap-1 rounded-md border px-2 py-0.5 text-[10px] font-bold tracking-wide`}
                    >
                      <span className={`w-1.5 h-1.5 rounded-full ${headerStatus.dotClassName}`} />
                      {headerStatus.label}
                    </span>
                  </div>
                  {selectedTask?.description ? (
                    <p className="ml-10 mt-1 max-w-3xl text-sm font-medium text-slate-500">
                      {selectedTask.description}
                    </p>
                  ) : !selectedTask && selectedService.description ? (
                    <p className="ml-10 mt-1 max-w-3xl text-sm font-medium text-slate-500">
                      {selectedService.description}
                    </p>
                  ) : null}
                </div>

                {/* Main Action Triggers */}
                <div className="flex flex-wrap gap-2 self-start sm:self-center">
                  {selectedTask ? (
                    <>
                      <button
                        aria-busy={isManualRunSubmitting && manualRunTaskId === selectedTask.id}
                        onClick={() => handleOpenManualRun(selectedTask.id)}
                        disabled={selectedTaskIsOffline || !selectedTaskCanRunOnce || !apiConnected || isPendingTaskToggle || isManualRunSubmitting}
                        aria-label={tr('button.runOnce')}
                        className="ui-pressable grid h-11 w-11 place-items-center rounded-md text-emerald-700 hover:bg-emerald-50 disabled:cursor-not-allowed disabled:opacity-50 lg:flex lg:h-auto lg:w-auto lg:min-w-[108px] lg:gap-1.5 lg:rounded-lg lg:border lg:border-emerald-200 lg:bg-white lg:px-4 lg:py-2 lg:text-xs lg:font-bold lg:shadow-xs"
                        type="button"
                      >
                        {isManualRunSubmitting && manualRunTaskId === selectedTask.id ? (
                          <RefreshCw className="w-4 h-4 animate-spin" />
                        ) : (
                          <Play className="w-4 h-4" />
                        )}
                        <span className="hidden lg:inline">{tr('button.runOnce')}</span>
                      </button>
                      <button
                        aria-busy={isPendingTaskToggle}
                        onClick={() => handleRestartTask(selectedTask.id)}
                        disabled={selectedTaskIsOffline || !selectedTaskCanRestart || !apiConnected || isPendingTaskToggle}
                        className="ui-pressable hidden min-w-[112px] items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-white px-4 py-2 text-xs font-bold text-indigo-600 shadow-xs hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50 lg:flex"
                        type="button"
                      >
                        {isPendingTaskToggle ? (
                          <RefreshCw className="w-4 h-4 animate-spin" />
                        ) : (
                          <RotateCcw className="w-4 h-4" />
                        )}
                        <span>
                          {isPendingTaskToggle ? tr('button.processing') : tr('button.restart')}
                        </span>
                      </button>
                      <button
                        aria-busy={isPendingTaskToggle}
                        onClick={() => handleToggleTaskStatus(selectedTask.id)}
                        disabled={selectedTaskIsOffline || !selectedTaskCanToggle || !apiConnected || isPendingTaskToggle}
                        className={`ui-pressable hidden min-w-[112px] items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-white px-4 py-2 text-xs font-bold shadow-xs lg:flex ${
                          selectedTaskToggleIsPause
                            ? 'text-rose-600 hover:bg-rose-50 border-rose-200'
                            : 'text-emerald-600 hover:bg-emerald-50 border-emerald-200'
                        } disabled:cursor-not-allowed disabled:opacity-50`}
                        type="button"
                      >
                        {isPendingTaskToggle ? (
                          <>
                            <RefreshCw className="w-4 h-4 animate-spin" />
                            <span>{tr('button.processing')}</span>
                          </>
                        ) : selectedTaskToggleIsPause ? (
                          <>
                            <Pause className="w-4 h-4" />
                            <span>{tr('button.stopTask')}</span>
                          </>
                        ) : (
                          <>
                            <Play className="w-4 h-4" />
                            <span>{selectedTask.viewStatus === 'paused' ? tr('button.resumeTask') : tr('button.stopTask')}</span>
                          </>
                        )}
                      </button>
                      <div className="relative isolate overflow-visible lg:hidden">
                        <button
                          ref={taskActionMenuTriggerRef}
                          aria-expanded={isTaskActionMenuOpen}
                          aria-haspopup="menu"
                          aria-label={tr('button.moreActions', { name: selectedTask.name })}
                          className="ui-pressable grid h-11 w-11 place-items-center rounded-md text-slate-500 hover:bg-slate-100"
                          onClick={() => setIsTaskActionMenuOpen((open) => !open)}
                          type="button"
                        >
                          <MoreHorizontal className="h-5 w-5" />
                        </button>
                        {isTaskActionMenuOpen && (
                          <div
                            ref={taskActionMenuRef}
                            aria-label={tr('button.moreActions', { name: selectedTask.name })}
                            className="ui-popover-enter absolute left-0 top-full z-40 mt-1 min-w-40 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 text-xs font-semibold shadow-lg"
                            role="menu"
                          >
                            <button
                              aria-busy={isPendingTaskToggle}
                              className="ui-pressable flex min-h-11 w-full items-center gap-2 px-3 text-left text-indigo-600 hover:bg-slate-50 disabled:opacity-50"
                              disabled={selectedTaskIsOffline || !selectedTaskCanRestart || !apiConnected || isPendingTaskToggle}
                              onClick={() => {
                                closeTaskActionMenu();
                                void handleRestartTask(selectedTask.id);
                              }}
                              role="menuitem"
                              type="button"
                            >
                              <RotateCcw className="h-4 w-4" />
                              {tr('button.restart')}
                            </button>
                            <button
                              aria-busy={isPendingTaskToggle}
                              className={`ui-pressable flex min-h-11 w-full items-center gap-2 px-3 text-left hover:bg-slate-50 disabled:opacity-50 ${selectedTaskToggleIsPause ? 'text-rose-600' : 'text-emerald-600'}`}
                              disabled={selectedTaskIsOffline || !selectedTaskCanToggle || !apiConnected || isPendingTaskToggle}
                              onClick={() => {
                                closeTaskActionMenu();
                                void handleToggleTaskStatus(selectedTask.id);
                              }}
                              role="menuitem"
                              type="button"
                            >
                              {selectedTaskToggleIsPause ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                              {selectedTask.viewStatus === 'paused' ? tr('button.resumeTask') : tr('button.stopTask')}
                            </button>
                          </div>
                        )}
                      </div>
                    </>
                  ) : (
                    <>
                      <button
                        aria-busy={isRestartingAll}
                        onClick={handleRestartAll}
                        disabled={isRestartingAll || !serviceHasOnlineInstances || !apiConnected}
                        className="ui-pressable flex min-w-[120px] items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-white px-4 py-2 text-xs font-bold text-slate-700 shadow-xs hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
                        type="button"
                      >
                        <RefreshCw className={`w-4 h-4 ${isRestartingAll ? 'animate-spin' : ''}`} />
                        <span>{isRestartingAll ? tr('button.restarting') : tr('button.restartAll')}</span>
                      </button>
                      <button
                        aria-busy={isDeploying}
                        onClick={handleDeployUpdate}
                        disabled={isDeploying || !serviceHasOnlineInstances || !apiConnected}
                        className="ui-pressable flex min-w-[128px] items-center justify-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-bold text-white shadow-xs hover:bg-indigo-800 disabled:cursor-not-allowed disabled:opacity-50"
                        type="button"
                      >
                        <Workflow className="w-4 h-4" />
                        <span>{isDeploying ? tr('button.deploying') : tr('button.deployUpdate')}</span>
                      </button>
                    </>
                  )}
                </div>
              </div>

              {/* Deploy rolling progress indicator */}
              {isDeploying && (
                <div className="ui-panel-state-enter space-y-2 rounded-lg border border-blue-200 bg-blue-50 p-4 font-medium">
                  <div className="flex justify-between items-center text-xs text-blue-700 font-bold">
                    <span>{tr('service.deployProgress')}</span>
                    <span>{deploymentProgress}%</span>
                  </div>
                  <div
                    aria-label={tr('service.deployProgress')}
                    aria-valuemax={100}
                    aria-valuemin={0}
                    aria-valuenow={deploymentProgress}
                    className="h-2 w-full overflow-hidden rounded-full bg-indigo-100"
                    role="progressbar"
                  >
                    <div
                      aria-hidden="true"
                      className="ui-progress-fill h-full rounded-full bg-indigo-600"
                      style={{ '--ui-progress': deploymentProgress / 100 } as CSSProperties}
                    />
                  </div>
                </div>
              )}

              {/* Global Reboot loading indicator */}
              {isRestartingAll && (
                <div className="ui-panel-state-enter flex items-center gap-4 rounded-lg bg-slate-900 p-5 text-white">
                  <RefreshCw className="w-6 h-6 animate-spin text-blue-400" />
                  <div>
                    <h4 className="font-bold text-sm">{tr('service.restartInitialized')}</h4>
                    <p className="text-xs text-slate-400">{tr('service.resyncingOffsets')}</p>
                  </div>
                </div>
              )}

              {/* --- STAGE: Task Detail Screen (drill down) --- */}
              {selectedTask ? (
                <div className="space-y-6">
                  {/* Stats Bento Grid for Selected Task */}
                  <div className="grid grid-cols-2 gap-2 lg:grid-cols-4 lg:gap-6">
                    {/* Stat: Last run */}
                    <div className="flex min-h-[52px] min-w-0 items-center justify-between rounded-xl border border-slate-200 bg-white px-3 py-2 shadow-xs lg:h-28 lg:flex-col lg:items-start lg:justify-between lg:p-5">
                      <span className="text-[10px] text-slate-400 uppercase font-bold tracking-wider block">
                        {tr('common.lastRun')}
                      </span>
                      <div className="shrink-0 text-lg font-bold text-slate-900 lg:mt-2 lg:text-2xl">
                        {formatRelativeTime(selectedTask.uptimeReferenceAt, tr)}
                      </div>
                      <span className="hidden text-[10px] font-medium text-slate-500 lg:block">{tr('task.latestActivity')}</span>
                    </div>

                    {/* Stat: Throughput */}
                    <div className="flex min-h-[52px] min-w-0 items-center justify-between rounded-xl border border-slate-200 bg-white px-3 py-2 shadow-xs lg:h-28 lg:flex-col lg:items-start lg:justify-between lg:p-5">
                      <span className="text-[10px] text-slate-400 uppercase font-bold tracking-wider block">
                        {tr('common.throughput')}
                      </span>
                      <div className="shrink-0 text-lg font-bold text-slate-900 lg:mt-2 lg:text-2xl">
                        {selectedTask.throughputPerMin}{' '}
                        <span className="text-xs text-slate-500 font-normal">/min</span>
                      </div>
                      <div className="hidden h-1 w-full overflow-hidden rounded-full bg-slate-100 lg:block">
                        <div
                          aria-hidden="true"
                          className="ui-progress-fill h-full rounded-full bg-indigo-600"
                          style={{ '--ui-progress': selectedTask.concurrency / 12 } as CSSProperties}
                        />
                      </div>
                    </div>

                    {/* Stat: Success Rate */}
                    <div className="flex min-h-[52px] min-w-0 items-center justify-between rounded-xl border border-slate-200 bg-white px-3 py-2 shadow-xs lg:h-28 lg:flex-col lg:items-start lg:justify-between lg:p-5">
                      <span className="text-[10px] text-slate-400 uppercase font-bold tracking-wider block">
                        {tr('task.successRate')}
                      </span>
                      <div className="shrink-0 text-lg font-bold text-emerald-600 lg:mt-2 lg:text-2xl">
                        {selectedTask.successRate}%
                      </div>
                      <span className="hidden text-[10px] font-medium text-emerald-500 lg:block">{tr('task.withinSla')}</span>
                    </div>

                    {/* Stat: Error Count */}
                    <div className="group relative flex min-h-[52px] min-w-0 items-center justify-between overflow-hidden rounded-xl border border-slate-200 bg-white px-3 py-2 shadow-xs lg:h-28 lg:flex-col lg:items-start lg:justify-between lg:p-5">
                      <div className="absolute inset-0 bg-rose-50/20 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none" />
                      <span className="text-[10px] text-slate-400 uppercase font-bold tracking-wider block">
                        {tr('task.errorCount15m')}
                      </span>
                      <div className="shrink-0 text-lg font-bold text-rose-600 lg:mt-2 lg:text-2xl">{selectedTask.errorCount}</div>
                    </div>
                  </div>

                  {/* Main dual column for Task Details */}
                  <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                    {/* Left 2/3 column: Topology + Resource chart */}
                    <div className="lg:col-span-2 space-y-6">
                      <TopologyFlow task={selectedTask} resourceCatalog={resourceCatalog} />
                      <ResourceChart
                        windows={taskMetricWindows}
                        lookbackMinutes={taskMetricLookbackMinutes}
                        onLookbackMinutesChange={setTaskMetricLookbackMinutes}
                        isLoading={isLoadingTaskMetrics}
                        error={taskMetricsError}
                      />
                    </div>

                    {/* Right 1/3 column: Config editor */}
                    <div className="lg:col-span-1">
                      <ConfigEditor task={selectedTask} />
                    </div>

                    <div className="lg:col-span-3">
                      <TaskEventDiagnostics
                        logs={taskEventLogs}
                        isLoading={isLoadingTaskEvents}
                        error={taskEventsError}
                        lookbackMinutes={taskEventLookbackMinutes}
                        onLookbackMinutesChange={handleTaskEventLookbackMinutesChange}
                        total={taskEventTotal}
                        limit={DEFAULT_TASK_EVENT_PAGE_SIZE}
                        offset={taskEventOffset}
                        onPageChange={setTaskEventOffset}
                      />
                    </div>
                  </div>
                </div>
              ) : (
                /* --- STAGE: Service tabs (Instances / Tasks / Config / Logs) --- */
                <div className="space-y-6">
                  <div className="flex flex-wrap gap-1 rounded-lg border border-slate-200 bg-white p-1 shadow-xs">
                    {SERVICE_TABS.map((tab) => {
                      const count = tab === 'Tasks' ? selectedService.totalTaskCount : tab === 'Instances' ? selectedService.totalInstances : null;
                      const isActive = activeTab === tab;

                      return (
                        <button
                          key={tab}
                          onClick={() => navigateToServiceTab(tab)}
                          className={`ui-pressable inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-bold ${
                            isActive
                              ? 'bg-indigo-600 text-white shadow-xs'
                              : 'text-slate-500 hover:bg-slate-50 hover:text-slate-800'
                          }`}
                        >
                          <span>{serviceTabLabel(tab, tr)}</span>
                          {count !== null && (
                            <span
                              className={`min-w-4 rounded-full px-1.5 py-0.5 text-[10px] leading-none ${
                                isActive ? 'bg-white/20 text-white' : 'bg-slate-100 text-slate-600'
                              }`}
                            >
                              {count}
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>

                  {/* Telemetry Stats cards block */}
                  <TelemetryStats service={selectedService} />

                  {/* Render based on selected Tab */}
                  {activeTab === 'Tasks' && (
                    <div className="ui-panel-state-enter">
                      <div className="flex justify-between items-center mb-4">
                        <h3 className="text-md font-bold text-slate-800">{tr('service.tasksTitle')}</h3>
                        <span className="text-xs text-slate-500 font-medium">{tr('service.taskHint')}</span>
                      </div>
                      <TasksList
                        tasks={activeServiceTasks}
                        onTaskSelect={(task) => navigateToTask(task.id)}
                        onRestartTask={handleRestartTask}
                        onToggleTaskStatus={handleToggleTaskStatus}
                        pendingTaskId={pendingTaskId}
                      />
                    </div>
                  )}

                  {activeTab === 'Instances' && (
                    <div className="ui-panel-state-enter">
                      <div className="flex justify-between items-center mb-4">
                        <h3 className="text-md font-bold text-slate-800">{tr('service.instancesTitle')}</h3>
                        <span className="text-xs text-slate-500 font-medium">{tr('service.instancesHint')}</span>
                      </div>
                      <InstancesTable
                        instances={activeServiceInstances}
                        onRestartInstance={handleRestartInstance}
                        onToggleInstance={handleToggleInstance}
                      />
                    </div>
                  )}

                  {activeTab === 'Logs' && (
                    <div className="ui-panel-state-enter flex h-[400px] flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-xs">
                      {/* Log view top toolbar */}
                      <div className="px-4 py-3 border-b border-slate-200 bg-slate-50 flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <Terminal className="w-4 h-4 text-slate-400" />
                          <span className="text-xs font-bold text-slate-700">{tr('logs.liveStream')}</span>
                        </div>
                        <button
                          onClick={() => setLogs([])}
                          className="ui-pressable rounded-sm text-xs font-bold text-indigo-600 hover:underline"
                          type="button"
                        >
                          {tr('button.clearStream')}
                        </button>
                      </div>

                      {/* Log Box Terminal */}
                      <div
                        ref={logTerminalRef}
                        className="flex-1 p-4 bg-[#0c1017] overflow-y-auto font-mono text-xs select-text"
                      >
                        {logs.map((log, idx) => {
                          const isWarn = log.level === 'warn';
                          const isError = log.level === 'error';
                          let tagClass = 'text-blue-400';
                          if (isWarn) tagClass = 'text-amber-400';
                          if (isError) tagClass = 'text-rose-400';

                          return (
                            <div key={idx} className="mb-2 text-[#b5c2d1] leading-relaxed">
                              <div className="flex">
                                <span className="text-slate-500 shrink-0 w-16">{log.timestamp}</span>
                                <span className={`font-bold shrink-0 w-16 ${tagClass}`}>
                                  [{log.level.toUpperCase()}]
                                </span>
                                <span className="text-slate-400 shrink-0 w-32 font-bold select-none truncate pr-2">
                                  {log.source}
                                </span>
                                <span className="text-slate-200 font-medium break-words min-w-0">{log.message}</span>
                              </div>
                              {log.traceback ? (
                                <details className="mt-1 max-w-full sm:ml-64">
                                  <summary className="cursor-pointer select-none text-[11px] font-bold text-rose-300">
                                    {tr('logs.traceback')}
                                  </summary>
                                  <pre className="mt-1 max-h-56 overflow-auto whitespace-pre-wrap break-words rounded border border-slate-700 bg-slate-950 p-3 text-[11px] leading-relaxed text-slate-100">
                                    {log.traceback}
                                  </pre>
                                </details>
                              ) : null}
                            </div>
                          );
                        })}
                      </div>

                      {/* Log bottom meta bar */}
                      <div className="px-4 py-2 border-t border-slate-200 bg-slate-50 text-[10px] font-semibold text-slate-500 flex justify-between">
                        <span>{apiConnected ? tr('logs.socketConnected') : tr('logs.notConnected')}</span>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
            </div>
          )}
        </main>
      </div>

      {manualRunTask && (
        <div className="fixed inset-0 z-40 flex items-center justify-center px-4 py-6">
          <button
            aria-label={tr('manualRun.cancel')}
            className="ui-dialog-backdrop absolute inset-0 bg-slate-950/35"
            onClick={() => closeManualRunDialog()}
            type="button"
          />
          <div
            ref={manualRunDialogRef}
            aria-busy={isManualRunSubmitting}
            aria-labelledby="manual-run-dialog-title"
            aria-modal="true"
            className="ui-dialog-enter relative z-10 flex max-h-[88vh] w-full max-w-2xl flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-2xl"
            role="dialog"
          >
            <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-5 py-4">
              <div>
                <h3 className="text-base font-extrabold text-slate-900" id="manual-run-dialog-title">
                  {tr('manualRun.title')}
                </h3>
                <p className="mt-1 text-xs font-medium text-slate-500">
                  {tr('manualRun.subtitle', { name: manualRunTask.name })}
                </p>
              </div>
              <button
                aria-label={tr('manualRun.cancel')}
                className="ui-pressable rounded-md p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={isManualRunSubmitting}
                onClick={() => closeManualRunDialog()}
                type="button"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="space-y-4 overflow-y-auto px-5 py-4">
              <section className="space-y-2">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
                    {tr('manualRun.targets')}
                  </span>
                  {manualRunTargets.length > 0 && (
                    <div className="flex items-center gap-3 text-xs font-bold">
                      <button
                        className="text-indigo-600 hover:text-indigo-800 disabled:opacity-50"
                        disabled={isManualRunSubmitting}
                        onClick={() => setManualRunTargetIds(manualRunTargets.map((target) => target.instanceId))}
                        type="button"
                      >
                        {tr('manualRun.selectAll')}
                      </button>
                      <button
                        className="text-slate-500 hover:text-slate-800 disabled:opacity-50"
                        disabled={isManualRunSubmitting}
                        onClick={() => setManualRunTargetIds([])}
                        type="button"
                      >
                        {tr('manualRun.clear')}
                      </button>
                    </div>
                  )}
                </div>

                {isManualRunLoadingTargets ? (
                  <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs font-semibold text-slate-500">
                    {tr('manualRun.loadingTargets')}
                  </div>
                ) : manualRunTargets.length === 0 ? (
                  <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs font-semibold text-slate-500">
                    {tr('manualRun.noTargets')}
                  </div>
                ) : (
                  <div className="grid gap-2">
                    {manualRunTargets.map((target) => {
                      const checked = manualRunTargetIds.includes(target.instanceId);
                      return (
                        <label
                          className={`flex items-center gap-3 rounded-lg border p-3 transition-colors ${
                            checked
                              ? 'border-indigo-200 bg-indigo-50'
                              : 'border-slate-200 bg-slate-50 hover:border-slate-300'
                          }`}
                          key={target.instanceId}
                        >
                          <input
                            checked={checked}
                            className="h-4 w-4 rounded border-slate-300 text-indigo-600"
                            disabled={isManualRunSubmitting}
                            onChange={() => toggleManualRunTarget(target.instanceId)}
                            type="checkbox"
                          />
                          <span className="min-w-0 flex-1">
                            <strong className="block truncate text-sm text-slate-800">{target.nodeName}</strong>
                            <span className="mt-0.5 block text-[11px] font-semibold text-slate-500">
                              {target.runnerCount ?? 0} runners · {target.inflightTaskCount ?? 0} inflight
                            </span>
                          </span>
                        </label>
                      );
                    })}
                  </div>
                )}
              </section>

              <label className="block space-y-2">
                <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
                  {tr('manualRun.payloadLabel')}
                </span>
                <textarea
                  className="h-48 w-full resize-y rounded-lg border border-slate-200 bg-slate-950 p-3 font-mono text-xs leading-relaxed text-slate-100 outline-hidden focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 disabled:opacity-50"
                  data-autofocus="true"
                  disabled={isManualRunSubmitting}
                  onChange={(event) => setManualRunPayloadText(event.target.value)}
                  placeholder={tr('manualRun.payloadPlaceholder')}
                  spellCheck={false}
                  value={manualRunPayloadText}
                />
              </label>

              <label className="block space-y-2">
                <span className="text-[11px] font-bold uppercase tracking-wider text-slate-400">
                  {tr('manualRun.reasonLabel')}
                </span>
                <textarea
                  className="h-20 w-full resize-y rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs font-semibold text-slate-700 outline-hidden focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 disabled:opacity-50"
                  disabled={isManualRunSubmitting}
                  onChange={(event) => setManualRunReason(event.target.value)}
                  placeholder={tr('manualRun.reasonPlaceholder')}
                  value={manualRunReason}
                />
              </label>

              {manualRunError && (
                <div className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs font-bold text-rose-700">
                  {manualRunError}
                </div>
              )}
            </div>

            <div className="flex justify-end gap-2 border-t border-slate-200 bg-slate-50 px-5 py-4">
              <button
                className="ui-pressable rounded-lg border border-slate-200 bg-white px-4 py-2 text-xs font-bold text-slate-600 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={isManualRunSubmitting}
                onClick={() => closeManualRunDialog()}
                type="button"
              >
                {tr('manualRun.cancel')}
              </button>
              <button
                aria-busy={isManualRunSubmitting}
                className="ui-pressable flex min-w-[104px] items-center justify-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-xs font-bold text-white shadow-xs hover:bg-indigo-800 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={isManualRunSubmitting || isManualRunLoadingTargets}
                onClick={() => void handleManualRunSubmit()}
                type="button"
              >
                {isManualRunSubmitting && <RefreshCw className="h-3.5 w-3.5 animate-spin" />}
                <span>{isManualRunSubmitting ? tr('manualRun.dispatching') : tr('manualRun.dispatch')}</span>
              </button>
            </div>
          </div>
        </div>
      )}

      <ToastViewport dismissLabel={tr('toast.dismiss')} onDismiss={dismissToast} toasts={toasts} />
    </div>
  );
}

export function getHeaderStatus(
  serviceViewStatus: Service['viewStatus'],
  taskViewStatus: Task['viewStatus'] | undefined,
  t: ReturnType<typeof useI18n>['t'],
) {
  // Both enums are lowercase and plane-derived. Task view status (when present)
  // takes precedence since it is the more specific signal.
  const resolvedStatus = taskViewStatus ?? serviceViewStatus;
  if (resolvedStatus === 'running') {
    return {
      label: t('common.running'),
      className: 'bg-emerald-50 text-emerald-700 border-emerald-200',
      dotClassName: 'bg-emerald-500',
    };
  }
  if (resolvedStatus === 'paused') {
    return {
      label: t('status.paused'),
      className: 'bg-sky-50 text-sky-700 border-sky-200',
      dotClassName: 'bg-sky-500',
    };
  }
  if (resolvedStatus === 'idle') {
    return {
      label: t('status.idle'),
      className: 'bg-slate-50 text-slate-600 border-slate-200',
      dotClassName: 'bg-slate-400',
    };
  }
  if (resolvedStatus === 'failed') {
    return {
      label: t('status.failed'),
      className: 'bg-amber-50 text-amber-700 border-amber-200',
      dotClassName: 'bg-amber-500',
    };
  }
  if (resolvedStatus === 'degraded') {
    return {
      label: t('common.degraded'),
      className: 'bg-amber-50 text-amber-700 border-amber-200',
      dotClassName: 'bg-amber-500',
    };
  }
  return {
    label: t('common.offline'),
    className: 'bg-slate-100 text-slate-600 border-slate-200',
    dotClassName: 'bg-slate-400',
  };
}

function serviceTabLabel(tab: ServiceTab, t: ReturnType<typeof useI18n>['t']): string {
  if (tab === 'Tasks') return t('tabs.tasks');
  if (tab === 'Instances') return t('tabs.instances');
  return t('tabs.logs');
}

export type EnvironmentFilter = 'all' | Environment;

const ENVIRONMENT_FILTER_STORAGE_KEY = 'onestep.controlPlane.environmentFilter';

const ENVIRONMENT_FILTER_ORDER: EnvironmentFilter[] = ['all', 'prod', 'staging', 'dev'];

function readEnvironmentFilter(): EnvironmentFilter {
  if (typeof window === 'undefined') return 'all';
  try {
    const stored = window.localStorage.getItem(ENVIRONMENT_FILTER_STORAGE_KEY);
    if (stored && (ENVIRONMENT_FILTER_ORDER as string[]).includes(stored)) {
      return stored as EnvironmentFilter;
    }
  } catch {
    // localStorage may be unavailable (private mode); fall back to default.
  }
  return 'all';
}

function writeEnvironmentFilter(value: EnvironmentFilter) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(ENVIRONMENT_FILTER_STORAGE_KEY, value);
  } catch {
    // Ignore persistence failures.
  }
}

function apiEnvironment(filter: EnvironmentFilter): Environment | undefined {
  return filter === 'all' ? undefined : filter;
}

function environmentFilterLabel(
  filter: EnvironmentFilter,
  t: ReturnType<typeof useI18n>['t'],
): string {
  if (filter === 'all') return t('environment.all');
  if (filter === 'prod') return t('environment.prod');
  if (filter === 'staging') return t('environment.staging');
  return t('environment.dev');
}
