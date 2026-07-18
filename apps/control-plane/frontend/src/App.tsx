import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import {
  dispatchInstanceCommand,
  dispatchServiceCommand,
  dispatchTaskCommand,
  formatUptime,
  getFanoutCommandIds,
  getApiErrorMessage,
  isAuthRequiredError,
  loadControlPlaneData,
  loadTaskMetricWindows,
  logoutConsole,
  pollTaskCommandCompletion,
  pollTaskPauseRequested,
  DEFAULT_TASK_METRIC_LOOKBACK_MINUTES,
  type Environment,
  type ServiceCommandFanoutResponse,
  type ServiceSummaryStats,
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
import ServicesList from './components/ServicesList';
import OverviewPage from './components/OverviewPage';
import TelemetryStats from './components/TelemetryStats';
import TasksList from './components/TasksList';
import InstancesTable from './components/InstancesTable';
import TopologyFlow from './components/TopologyFlow';
import ConfigEditor from './components/ConfigEditor';
import ResourceChart from './components/ResourceChart';
import LoginPage from './components/LoginPage';
import NotificationSettingsPage from './components/NotificationSettingsPage';
import LocaleSwitcher from './components/LocaleSwitcher';
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
  Square,
  RotateCcw,
  RefreshCw,
  FileCode,
  Workflow,
  ChevronRight,
  Terminal,
  ArrowLeft,
  Globe,
  Check,
} from 'lucide-react';

const EMPTY_SERVICE: Service = {
  id: '',
  name: '',
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

function getTaskToggleCommand(task: Task | null | undefined): 'pause_task' | 'resume_task' | null {
  if (task?.viewStatus === 'running') return 'pause_task';
  if (task?.viewStatus === 'paused') return 'resume_task';
  return null;
}

function isTaskToggleSupported(task: Task | null | undefined): boolean {
  const command = getTaskToggleCommand(task);
  return command !== null && taskSupportsCommand(task, command);
}

const SERVICE_TABS: ServiceTab[] = ['Tasks', 'Instances', 'Configuration', 'Logs'];

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
  const [apiConnected, setApiConnected] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [isLoadingApi, setIsLoadingApi] = useState(true);
  const [taskMetricWindows, setTaskMetricWindows] = useState<TaskMetricChartPointSummary[]>([]);
  const [taskMetricLookbackMinutes, setTaskMetricLookbackMinutes] = useState(DEFAULT_TASK_METRIC_LOOKBACK_MINUTES);
  const [taskMetricsError, setTaskMetricsError] = useState<string | null>(null);
  const [isLoadingTaskMetrics, setIsLoadingTaskMetrics] = useState(false);

  // --- UI Navigation State ---
  const [routePath, setRoutePath] = useState(() => `${window.location.pathname}${window.location.search}`);
  const [currentView, setCurrentView] = useState<ControlPlaneView>(initialRouteState.currentView);
  const [selectedServiceId, setSelectedServiceId] = useState<string>(initialRouteState.selectedServiceId ?? '');
  const [activeTab, setActiveTab] = useState<ServiceTab>(initialRouteState.activeTab);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(initialRouteState.selectedTaskId);

  // --- Global Environment Filter ---
  const [environmentFilter, setEnvironmentFilter] = useState<EnvironmentFilter>(() => readEnvironmentFilter());
  const [isEnvMenuOpen, setIsEnvMenuOpen] = useState(false);
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
  // Task id currently awaiting a pause/resume outcome from the worker (command
  // dispatched, polling pause_requested until it reflects the new state or times
  // out). Drives the Loading state on the toggle button for that task.
  const [pendingTaskId, setPendingTaskId] = useState<string | null>(null);

  // --- Toast Notifications ---
  const [toasts, setToasts] = useState<{ id: string; message: string; type: 'success' | 'info' | 'warn' }[]>([]);

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

  const addToast = (message: string, type: 'success' | 'info' | 'warn' = 'success') => {
    const id = Math.random().toString(36).substr(2, 9);
    setToasts((prev) => [...prev, { id, message, type }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4000);
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
      const data = await loadControlPlaneData(targetServiceId, apiEnvironment(targetEnvironment));
      if (!isLatestRequest()) {
        return;
      }
      setServices(data.services);
      setTasks(data.tasks);
      setInstances(data.instances);
      setLogs(data.logs.length > 0 ? data.logs : []);
      setServiceSummary(data.serviceSummary);
      setSourceKindCounts(data.sourceKindCounts);
      setApiConnected(true);
      setApiError(null);
      if (data.selectedServiceId !== targetServiceId) {
        setSelectedServiceId(data.selectedServiceId);
      }
      if (!silent) {
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
  const serviceHasOnlineInstances = selectedService.activeInstances > 0;
  const selectedTaskIsOffline = selectedTask?.viewStatus === 'offline' || !serviceHasOnlineInstances;
  const selectedTaskCanToggle = isTaskToggleSupported(selectedTask);
  const selectedTaskCanRestart = taskSupportsCommand(selectedTask, 'restart_task');
  const isPendingTaskToggle = !!selectedTask && pendingTaskId === selectedTask.id;
  const headerStatus = getHeaderStatus(selectedService.viewStatus, selectedTask?.viewStatus, tr);

  // --- Live Terminal Logs Simulator ---
  const logTerminalRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (logTerminalRef.current) {
      logTerminalRef.current.scrollTop = logTerminalRef.current.scrollHeight;
    }
  }, [logs]);

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
    <div className="bg-slate-50 text-slate-800 font-sans min-h-screen flex overflow-hidden">
      {/* --- Sidebar Navigator panel --- */}
      <Sidebar
        currentView={currentView}
        isLogoutPending={isLogoutPending}
        onLogout={() => void handleLogout()}
        onViewChange={navigateToView}
      />

      {/* --- Main Content Panel --- */}
      <div className="flex-1 flex flex-col ml-[240px] h-screen overflow-hidden">
        {/* --- Core Content Stage Canvas --- */}
        <main className="flex-1 overflow-y-auto p-6 bg-slate-50">
          <div className="max-w-7xl mx-auto mb-4 flex items-center justify-between rounded-lg border border-slate-200 bg-white px-4 py-2 text-xs font-semibold text-slate-600 shadow-xs">
            <div className="flex items-center gap-3">
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
                  onClick={() => setIsEnvMenuOpen((open) => !open)}
                  className="flex items-center gap-1.5 rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] font-bold text-slate-700 transition-colors hover:bg-slate-100"
                  aria-haspopup="listbox"
                  aria-expanded={isEnvMenuOpen}
                >
                  <Globe className="h-3 w-3 text-indigo-600" />
                  <span>{tr('top.environment')}</span>
                  <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-indigo-700">
                    {environmentFilterLabel(environmentFilter, tr)}
                  </span>
                </button>
                {isEnvMenuOpen && (
                  <>
                    <div
                      className="fixed inset-0 z-30"
                      onClick={() => setIsEnvMenuOpen(false)}
                      aria-hidden="true"
                    />
                    <ul
                      role="listbox"
                      className="absolute right-0 z-40 mt-1 w-44 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 shadow-lg font-medium text-xs"
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
                                setIsEnvMenuOpen(false);
                              }}
                              className={`flex w-full items-center justify-between px-3 py-1.5 text-left transition-colors ${
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
                  </>
                )}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <LocaleSwitcher />
              <button
                onClick={() => void refreshControlPlaneData(selectedServiceId)}
                disabled={isLoadingApi}
                className="flex items-center gap-1 rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] font-bold text-slate-600 transition-colors hover:bg-slate-100 disabled:opacity-50"
              >
                <RefreshCw className={`h-3 w-3 ${isLoadingApi ? 'animate-spin' : ''}`} />
                <span>{isLoadingApi ? tr('button.refreshing') : tr('button.refresh')}</span>
              </button>
            </div>
          </div>

          {services.length === 0 ? (
            <div className="max-w-7xl mx-auto rounded-xl border border-slate-200 bg-white p-8 text-center shadow-xs">
              <RefreshCw className={`mx-auto mb-3 h-5 w-5 text-slate-400 ${isLoadingApi ? 'animate-spin' : ''}`} />
              <h2 className="text-sm font-bold text-slate-800">
                {isLoadingApi ? tr('api.connecting') : tr('api.noServices')}
              </h2>
              <p className="mt-1 text-xs font-medium text-slate-500">
                {apiError ? tr('api.noServicesHint', { error: apiError }) : tr('api.waitingForServices')}
              </p>
            </div>
          ) : (
            <>
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
            <div className="max-w-7xl mx-auto space-y-6">
              {/* Breadcrumb navigator under Command Bar */}
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 animate-fadeIn">
                <div>
                  <nav aria-label="Breadcrumb" className="flex items-center text-slate-400 font-medium text-xs mb-1">
                    <button
                      type="button"
                      onClick={navigateToServicesList}
                      className="rounded-sm hover:text-indigo-600 focus:outline-hidden focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 transition-colors"
                    >
                      {tr('nav.services')}
                    </button>
                    <ChevronRight className="w-3.5 h-3.5 mx-1" />
                    {selectedTask ? (
                      <button
                        type="button"
                        onClick={() => navigateToService(selectedServiceId)}
                        className="rounded-sm text-slate-800 font-bold hover:text-indigo-600 focus:outline-hidden focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 transition-colors"
                      >
                        {selectedService.name.split(' ')[0]}
                      </button>
                    ) : (
                      <span className="text-slate-800 font-bold">{selectedService.name.split(' ')[0]}</span>
                    )}
                    {selectedTask && (
                      <>
                        <ChevronRight className="w-3.5 h-3.5 mx-1" />
                        <button
                          type="button"
                          onClick={() => navigateToServiceTab('Tasks')}
                          className="rounded-sm text-slate-500 font-semibold hover:text-indigo-600 focus:outline-hidden focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 transition-colors"
                        >
                          {tr('tabs.tasks')}
                        </button>
                        <ChevronRight className="w-3.5 h-3.5 mx-1" />
                        <span className="text-slate-900 font-bold">{selectedTask.name}</span>
                      </>
                    )}
                  </nav>

                  <div className="flex items-center gap-3 mt-1.5">
                    <button
                      onClick={navigateToServicesList}
                      className="p-1.5 rounded-md text-slate-400 hover:text-indigo-600 hover:bg-slate-100 transition-colors"
                      title={tr('nav.services')}
                      aria-label={tr('nav.services')}
                    >
                      <ArrowLeft className="w-4 h-4" />
                    </button>
                    <h2 className="text-2xl font-extrabold text-slate-900 tracking-tight font-sans">
                      {selectedTask ? selectedTask.name : tr('service.detail')}
                    </h2>
                    <span
                      className={`${headerStatus.className} px-2 py-0.5 rounded-md font-bold tracking-wide text-[10px] flex items-center gap-1 border`}
                    >
                      <span className={`w-1.5 h-1.5 rounded-full ${headerStatus.dotClassName}`} />
                      {headerStatus.label}
                    </span>
                  </div>
                </div>

                {/* Main Action Triggers */}
                <div className="flex gap-2 self-start sm:self-center">
                  {selectedTask ? (
                    <>
                      <button
                        onClick={() => handleRestartTask(selectedTask.id)}
                        disabled={selectedTaskIsOffline || !selectedTaskCanRestart || !apiConnected || isPendingTaskToggle}
                        className="flex items-center gap-1.5 px-4 py-2 border border-slate-200 rounded-lg text-indigo-600 hover:bg-slate-100 transition-colors text-xs font-bold bg-white shadow-xs disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {isPendingTaskToggle ? (
                          <RefreshCw className="w-4 h-4 animate-spin" />
                        ) : (
                          <RotateCcw className="w-4 h-4" />
                        )}
                        <span>
                          {isPendingTaskToggle
                            ? tr('button.processing')
                            : selectedTaskCanRestart
                            ? tr('button.restart')
                            : tr('button.unavailable')}
                        </span>
                      </button>
                      <button
                        onClick={() => handleToggleTaskStatus(selectedTask.id)}
                        disabled={selectedTaskIsOffline || !selectedTaskCanToggle || !apiConnected || isPendingTaskToggle}
                        className={`flex items-center gap-1.5 px-4 py-2 border border-slate-200 rounded-lg transition-colors text-xs font-bold bg-white shadow-xs ${
                          selectedTask.viewStatus === 'running'
                            ? 'text-rose-600 hover:bg-rose-50 border-rose-200'
                            : 'text-emerald-600 hover:bg-emerald-50 border-emerald-200'
                        } disabled:cursor-not-allowed disabled:opacity-50`}
                      >
                        {isPendingTaskToggle ? (
                          <>
                            <RefreshCw className="w-4 h-4 animate-spin" />
                            <span>{tr('button.processing')}</span>
                          </>
                        ) : selectedTask.viewStatus === 'running' ? (
                          <>
                            <Square className="w-4 h-4" />
                            <span>{tr('button.stopTask')}</span>
                          </>
                        ) : (
                          <>
                            <Play className="w-4 h-4" />
                            <span>
                              {selectedTask.viewStatus === 'paused'
                                ? tr('button.resumeTask')
                                : tr('button.unavailable')}
                            </span>
                          </>
                        )}
                      </button>
                      <button
                        onClick={() => {
                          navigateToServiceTab('Configuration');
                        }}
                        className="flex items-center gap-1.5 px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-800 transition-colors text-xs font-bold shadow-xs"
                      >
                        <FileCode className="w-4 h-4" />
                        <span>{tr('button.editConfig')}</span>
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        onClick={handleRestartAll}
                        disabled={isRestartingAll || !serviceHasOnlineInstances || !apiConnected}
                        className="flex items-center gap-1.5 px-4 py-2 border border-slate-200 rounded-lg text-slate-700 hover:bg-slate-100 transition-colors text-xs font-bold bg-white shadow-xs disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <RefreshCw className={`w-4 h-4 ${isRestartingAll ? 'animate-spin' : ''}`} />
                        <span>{isRestartingAll ? tr('button.restarting') : tr('button.restartAll')}</span>
                      </button>
                      <button
                        onClick={handleDeployUpdate}
                        disabled={isDeploying || !serviceHasOnlineInstances || !apiConnected}
                        className="flex items-center gap-1.5 px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-800 transition-colors text-xs font-bold shadow-xs disabled:cursor-not-allowed disabled:opacity-50"
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
                <div className="bg-blue-50 border border-blue-200 p-4 rounded-xl space-y-2 animate-fadeIn font-medium">
                  <div className="flex justify-between items-center text-xs text-blue-700 font-bold">
                    <span>{tr('service.deployProgress')}</span>
                    <span>{deploymentProgress}%</span>
                  </div>
                  <div className="w-full h-2 bg-indigo-100 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-indigo-600 transition-all duration-300"
                      style={{ width: `${deploymentProgress}%` }}
                    />
                  </div>
                </div>
              )}

              {/* Global Reboot loading indicator */}
              {isRestartingAll && (
                <div className="bg-slate-900 text-white rounded-xl p-5 flex items-center gap-4 animate-pulse">
                  <RefreshCw className="w-6 h-6 animate-spin text-blue-400" />
                  <div>
                    <h4 className="font-bold text-sm">{tr('service.restartInitialized')}</h4>
                    <p className="text-xs text-slate-400">{tr('service.resyncingOffsets')}</p>
                  </div>
                </div>
              )}

              {/* --- STAGE: Task Detail Screen (drill down) --- */}
              {selectedTask ? (
                <div className="space-y-6 animate-fadeIn">
                  {/* Stats Bento Grid for Selected Task */}
                  <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-6">
                    {/* Stat: Uptime */}
                    <div className="bg-white border border-slate-200 rounded-xl p-5 flex flex-col justify-between h-28 shadow-xs">
                      <span className="text-[10px] text-slate-400 uppercase font-bold tracking-wider block">
                        {tr('common.uptime')}
                      </span>
                      <div className="text-2xl font-bold text-slate-900 mt-2">{formatUptime(selectedTask.uptimeReferenceAt)}</div>
                      <span className="text-[10px] text-slate-500 font-medium">{tr('task.activeStreamStatus')}</span>
                    </div>

                    {/* Stat: Throughput */}
                    <div className="bg-white border border-slate-200 rounded-xl p-5 flex flex-col justify-between h-28 shadow-xs">
                      <span className="text-[10px] text-slate-400 uppercase font-bold tracking-wider block">
                        {tr('common.throughput')}
                      </span>
                      <div className="text-2xl font-bold text-slate-900 mt-2">
                        {selectedTask.throughputPerMin}{' '}
                        <span className="text-xs text-slate-500 font-normal">/min</span>
                      </div>
                      <div className="w-full h-1 bg-slate-100 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-indigo-600 rounded-full"
                          style={{ width: `${(selectedTask.concurrency / 12) * 100}%` }}
                        />
                      </div>
                    </div>

                    {/* Stat: Success Rate */}
                    <div className="bg-white border border-slate-200 rounded-xl p-5 flex flex-col justify-between h-28 shadow-xs">
                      <span className="text-[10px] text-slate-400 uppercase font-bold tracking-wider block">
                        {tr('task.successRate')}
                      </span>
                      <div className="text-2xl font-bold text-emerald-600 mt-2">
                        {selectedTask.successRate}%
                      </div>
                      <span className="text-[10px] text-emerald-500 font-medium">{tr('task.withinSla')}</span>
                    </div>

                    {/* Stat: Error Count */}
                    <div className="bg-white border border-slate-200 rounded-xl p-5 flex flex-col justify-between h-28 shadow-xs group relative overflow-hidden">
                      <div className="absolute inset-0 bg-rose-50/20 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none" />
                      <span className="text-[10px] text-slate-400 uppercase font-bold tracking-wider block">
                        {tr('task.errorCount15m')}
                      </span>
                      <div className="text-2xl font-bold text-rose-600 mt-2">{selectedTask.errorCount}</div>
                    </div>
                  </div>

                  {/* Main dual column for Task Details */}
                  <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                    {/* Left 2/3 column: Topology + Resource chart */}
                    <div className="lg:col-span-2 space-y-6">
                      <TopologyFlow task={selectedTask} />
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
                  </div>
                </div>
              ) : (
                /* --- STAGE: Service tabs (Instances / Tasks / Config / Logs) --- */
                <div className="space-y-6">
                  <div className="flex flex-wrap gap-1 rounded-lg border border-slate-200 bg-white p-1 shadow-xs">
                    {SERVICE_TABS.map((tab) => (
                      <button
                        key={tab}
                        onClick={() => navigateToServiceTab(tab)}
                        className={`rounded-md px-3 py-1.5 text-xs font-bold transition-colors ${
                          activeTab === tab
                            ? 'bg-indigo-600 text-white shadow-xs'
                            : 'text-slate-500 hover:bg-slate-50 hover:text-slate-800'
                        }`}
                      >
                        {serviceTabLabel(tab, tr)}
                      </button>
                    ))}
                  </div>

                  {/* Telemetry Stats cards block */}
                  <TelemetryStats service={selectedService} />

                  {/* Render based on selected Tab */}
                  {activeTab === 'Tasks' && (
                    <div className="animate-fadeIn">
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
                    <div className="animate-fadeIn">
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

                  {activeTab === 'Configuration' && (
                    <div className="bg-white border border-slate-200 rounded-xl p-6 space-y-4 animate-fadeIn font-sans">
                      <div className="flex items-center gap-3 border-b border-slate-100 pb-4">
                        <div className="w-10 h-10 rounded-lg bg-indigo-50 flex items-center justify-center text-indigo-600">
                          <FileCode className="w-5 h-5" />
                        </div>
                        <div>
                          <h3 className="text-sm font-bold text-slate-800">{tr('config.globalSpec')}</h3>
                          <p className="text-xs text-slate-500 font-medium">{tr('config.globalSpecDescription')}</p>
                        </div>
                      </div>

                      <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50 p-4">
                        <p className="text-sm font-bold text-slate-700">{tr('config.noServiceConfig')}</p>
                        <p className="mt-1 text-xs font-medium text-slate-500">{tr('config.noServiceConfigHint')}</p>
                      </div>
                    </div>
                  )}

                  {activeTab === 'Logs' && (
                    <div className="bg-white border border-slate-200 rounded-xl overflow-hidden shadow-xs animate-fadeIn flex flex-col h-[400px]">
                      {/* Log view top toolbar */}
                      <div className="px-4 py-3 border-b border-slate-200 bg-slate-50 flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <Terminal className="w-4 h-4 text-slate-400" />
                          <span className="text-xs font-bold text-slate-700">{tr('logs.liveStream')}</span>
                        </div>
                        <button
                          onClick={() => setLogs([])}
                          className="text-xs font-bold text-indigo-600 hover:underline"
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
                            <div key={idx} className="mb-1 text-[#b5c2d1] leading-relaxed flex">
                              <span className="text-slate-500 shrink-0 w-16">{log.timestamp}</span>
                              <span className={`font-bold shrink-0 w-16 ${tagClass}`}>
                                [{log.level.toUpperCase()}]
                              </span>
                              <span className="text-slate-400 shrink-0 w-32 font-bold select-none truncate pr-2">
                                {log.source}
                              </span>
                              <span className="text-slate-200 font-medium">{log.message}</span>
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
            </>
          )}
        </main>
      </div>

      {/* --- Floating Dismissible Toast Notification System --- */}
      <div className="fixed bottom-6 right-6 z-50 space-y-2.5 max-w-sm">
        {toasts.map((toast) => {
          let bg = 'bg-white border-slate-200 text-slate-800';
          let indicatorColor = 'bg-indigo-600';

          if (toast.type === 'info') {
            bg = 'bg-[#1a1c24] border-slate-700/60 text-white';
            indicatorColor = 'bg-indigo-600';
          } else if (toast.type === 'warn') {
            bg = 'bg-rose-50 border-rose-200 text-rose-900';
            indicatorColor = 'bg-rose-500';
          }

          return (
            <div
              key={toast.id}
              className={`border p-4 rounded-xl shadow-lg flex items-center gap-3 animate-slideIn ${bg} font-medium text-xs`}
            >
              <div className={`w-2 h-2 rounded-full ${indicatorColor} animate-pulse shrink-0`} />
              <span className="flex-1 leading-relaxed font-semibold">{toast.message}</span>
              <button
                onClick={() => setToasts((prev) => prev.filter((t) => t.id !== toast.id))}
                className="text-slate-400 hover:text-slate-600 font-bold shrink-0 p-1"
              >
                ×
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function getHeaderStatus(
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
  if (resolvedStatus === 'failed' || resolvedStatus === 'degraded') {
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
  if (tab === 'Configuration') return t('tabs.configuration');
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
