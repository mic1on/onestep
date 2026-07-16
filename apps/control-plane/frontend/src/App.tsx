import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import {
  dispatchInstanceCommand,
  dispatchServiceCommand,
  dispatchTaskCommand,
  getApiErrorMessage,
  isAuthRequiredError,
  loadControlPlaneData,
} from './api';
import {
  INITIAL_SERVICES,
  INITIAL_TASKS,
  INITIAL_INSTANCES,
  INITIAL_LOGS,
  MOCK_TRACES,
} from './initialData';
import { Service, Task, Instance, LogEntry } from './types';
import Sidebar from './components/Sidebar';
import TopAppBar from './components/TopAppBar';
import TelemetryStats from './components/TelemetryStats';
import TasksList from './components/TasksList';
import InstancesTable from './components/InstancesTable';
import TopologyFlow from './components/TopologyFlow';
import ConfigEditor from './components/ConfigEditor';
import ResourceChart from './components/ResourceChart';
import LoginPage from './components/LoginPage';
import NotificationSettingsPage from './components/NotificationSettingsPage';
import type { ControlPlaneView } from './components/Sidebar';
import { useI18n } from './i18n';
import {
  Play,
  Square,
  RotateCcw,
  RefreshCw,
  Plus,
  Send,
  AlertCircle,
  FileCode,
  Network,
  Activity,
  Workflow,
  Sparkles,
  ChevronRight,
  Database,
  History,
  CheckCircle,
  Terminal,
} from 'lucide-react';

export default function App() {
  const { t: tr } = useI18n();

  // --- API-backed state with local demo defaults ---
  const [services, setServices] = useState<Service[]>(INITIAL_SERVICES);
  const [tasks, setTasks] = useState<Task[]>(INITIAL_TASKS);
  const [instances, setInstances] = useState<Instance[]>(INITIAL_INSTANCES);
  const [logs, setLogs] = useState<LogEntry[]>(INITIAL_LOGS);
  const [apiConnected, setApiConnected] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [isLoadingApi, setIsLoadingApi] = useState(false);

  // --- UI Navigation State ---
  const [routePath, setRoutePath] = useState(() => `${window.location.pathname}${window.location.search}`);
  const [currentView, setCurrentView] = useState<ControlPlaneView>('services');
  const [selectedServiceId, setSelectedServiceId] = useState<string>('user-auth-service');
  const [activeTab, setActiveTab] = useState<'Tasks' | 'Instances' | 'Configuration' | 'Logs'>('Tasks');
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);

  // --- Trace Viewer State ---
  const [showTraces, setShowTraces] = useState(false);

  // --- Loading / Overlay State ---
  const [isRestartingAll, setIsRestartingAll] = useState(false);
  const [isDeploying, setIsDeploying] = useState(false);
  const [deploymentProgress, setDeploymentProgress] = useState(0);

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

  const refreshControlPlaneData = async (targetServiceId = selectedServiceId, silent = false) => {
    setIsLoadingApi(true);
    try {
      const data = await loadControlPlaneData(targetServiceId);
      setServices(data.services);
      setTasks(data.tasks);
      setInstances(data.instances);
      setLogs(data.logs.length > 0 ? data.logs : []);
      setApiConnected(true);
      setApiError(null);
      if (data.selectedServiceId !== targetServiceId) {
        setSelectedServiceId(data.selectedServiceId);
      }
      if (!silent) {
        addToast(tr('toast.controlPlaneRefreshed'), 'success');
      }
    } catch (error) {
      if (isAuthRequiredError(error)) {
        setApiConnected(false);
        setApiError(null);
        redirectToLogin();
        return;
      }
      const message = getApiErrorMessage(error);
      setApiConnected(false);
      setApiError(message);
      if (!silent) {
        addToast(tr('toast.apiFailed', { message }), 'warn');
      }
    } finally {
      setIsLoadingApi(false);
    }
  };

  const handleApiActionError = (error: unknown, fallbackMessage: string) => {
    if (isAuthRequiredError(error)) {
      redirectToLogin();
      return;
    }
    addToast(tr('toast.withError', { action: fallbackMessage, message: getApiErrorMessage(error) }), 'warn');
  };

  useEffect(() => {
    if (routePath.startsWith('/login')) return;
    void refreshControlPlaneData(selectedServiceId, true);
  }, [routePath]);

  useEffect(() => {
    if (apiConnected) {
      void refreshControlPlaneData(selectedServiceId, true);
    }
  }, [selectedServiceId]);

  // --- Active Objects Selectors ---
  const selectedService = useMemo(() => {
    return services.find((s) => s.id === selectedServiceId) || services[0] || INITIAL_SERVICES[0];
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

  // --- Live Terminal Logs Simulator ---
  const logTerminalRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (logTerminalRef.current) {
      logTerminalRef.current.scrollTop = logTerminalRef.current.scrollHeight;
    }
  }, [logs]);

  useEffect(() => {
    if (apiConnected) return;
    // Append a simulated log entry every 4 seconds to make the O&M plane active and alive!
    const logInterval = setInterval(() => {
      const activeTasks = tasks.filter((t) => t.status === 'Running' && t.serviceId === selectedServiceId);
      if (activeTasks.length === 0) return;

      const randomTask = activeTasks[Math.floor(Math.random() * activeTasks.length)];
      const templates = [
        `Processed ${Math.floor(1000 + Math.random() * 4000)} events. Sink commit complete.`,
        `Partition offsets committed back to broker coordinator. Lag is 0.`,
        `Healthy signal received from heartbeat telemetry agent.`,
        `Optimistic locking threshold normal (latency = ${Math.floor(10 + Math.random() * 30)}ms).`,
        `Buffer flush committed to storage engine replica set.`,
      ];

      const levels: ('info' | 'warn')[] = ['info', 'info', 'info', 'info', 'warn'];
      const randomLvl = levels[Math.floor(Math.random() * levels.length)];
      const randomMessage = templates[Math.floor(Math.random() * templates.length)];

      const now = new Date();
      const timeStr = now.toTimeString().split(' ')[0];

      setLogs((prev) => [
        ...prev,
        {
          timestamp: timeStr,
          level: randomLvl,
          message: randomMessage,
          source: randomTask.name,
        },
      ]);
    }, 4000);

    return () => clearInterval(logInterval);
  }, [apiConnected, tasks, selectedServiceId]);

  // --- Interactive Control plane Handlers ---

  // Restart All Service Components
  const handleRestartAll = async () => {
    if (selectedService.apiName && selectedService.environment) {
      setIsRestartingAll(true);
      addToast(tr('toast.restartAllDispatch', { name: selectedService.name }), 'info');
      try {
        await dispatchServiceCommand(selectedService, 'restart');
        addToast(tr('toast.restartAccepted', { name: selectedService.name }), 'success');
        await refreshControlPlaneData(selectedService.id, true);
      } catch (error) {
        handleApiActionError(error, tr('error.restartFailed'));
      } finally {
        setIsRestartingAll(false);
      }
      return;
    }

    setIsRestartingAll(true);
    addToast(tr('toast.restartAllLocal', { name: selectedService.name }), 'info');

    // Simulate task status cycles
    setTasks((prev) =>
      prev.map((t) => {
        if (t.serviceId === selectedServiceId) {
          return { ...t, status: 'Idle' };
        }
        return t;
      })
    );

    setTimeout(() => {
      setIsRestartingAll(false);
      setTasks((prev) =>
        prev.map((t) => {
          if (t.serviceId === selectedServiceId) {
            return { ...t, status: 'Running' };
          }
          return t;
        })
      );
      addToast(tr('toast.restartAllComplete', { name: selectedService.name }), 'success');

      // Append logs
      const timeStr = new Date().toTimeString().split(' ')[0];
      setLogs((prev) => [
        ...prev,
        {
          timestamp: timeStr,
          level: 'info',
          message: `Control Plane initiated manual service-wide reboot of ${selectedService.name}.`,
          source: 'System',
        },
        {
          timestamp: timeStr,
          level: 'info',
          message: `All pipeline targets successfully re-synchronized. Health is 100%.`,
          source: 'System',
        },
      ]);
    }, 1800);
  };

  // Deploy Config / Code Update Simulation
  const handleDeployUpdate = async () => {
    if (selectedService.apiName && selectedService.environment) {
      setIsDeploying(true);
      setDeploymentProgress(30);
      addToast(tr('toast.deployDispatch', { name: selectedService.name }), 'info');
      try {
        await dispatchServiceCommand(selectedService, 'sync_now');
        setDeploymentProgress(100);
        addToast(tr('toast.deployAccepted', { name: selectedService.name }), 'success');
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

    setIsDeploying(true);
    setDeploymentProgress(0);
    addToast(tr('toast.rollingDeployStart', { name: selectedService.name }), 'info');

    const interval = setInterval(() => {
      setDeploymentProgress((p) => {
        if (p >= 100) {
          clearInterval(interval);
          setIsDeploying(false);
          addToast(tr('toast.rollingDeployComplete'), 'success');

          // Bump versions of all instances
          setInstances((prev) =>
            prev.map((inst) => {
              if (inst.serviceId === selectedServiceId) {
                return { ...inst, version: 'v1.2.2', status: 'Running' };
              }
              return inst;
            })
          );

          // Append audit logs
          const timeStr = new Date().toTimeString().split(' ')[0];
          setLogs((prev) => [
            ...prev,
            {
              timestamp: timeStr,
              level: 'info',
              message: `Rolling deployment for version v1.2.2 finished successfully.`,
              source: 'Deployment',
            },
            {
              timestamp: timeStr,
              level: 'info',
              message: `Healthy ingress validation complete across all active container partitions.`,
              source: 'Deployment',
            },
          ]);

          return 100;
        }
        return p + 10;
      });
    }, 250);
  };

  // Toggle Task (Running <-> Stopped)
  const handleToggleTaskStatus = async (taskId: string) => {
    const task = tasks.find((item) => item.id === taskId);
    if (task?.apiName) {
      const kind = task.status === 'Running' ? 'pause_task' : 'resume_task';
      addToast(tr('toast.taskDispatch', { kind: kind.replace('_', ' '), name: task.name }), 'info');
      try {
        await dispatchTaskCommand(task, kind);
        addToast(tr('toast.commandAccepted', { name: task.name }), 'success');
        await refreshControlPlaneData(task.serviceId, true);
      } catch (error) {
        handleApiActionError(error, tr('error.taskCommandFailed'));
      }
      return;
    }

    setTasks((prev) =>
      prev.map((t) => {
        if (t.id === taskId) {
          const newStatus = t.status === 'Running' ? 'Stopped' : 'Running';
          addToast(tr('toast.taskStatusChanged', { name: t.name, status: newStatus.toUpperCase() }), newStatus === 'Running' ? 'success' : 'warn');
          return { ...t, status: newStatus };
        }
        return t;
      })
    );
  };

  // Restart Specific Task
  const handleRestartTask = async (taskId: string) => {
    const task = tasks.find((item) => item.id === taskId);
    if (task?.apiName) {
      addToast(tr('toast.taskRestartDispatch', { name: task.name }), 'info');
      try {
        await dispatchTaskCommand(task, 'pause_task');
        await dispatchTaskCommand(task, 'resume_task');
        addToast(tr('toast.taskRestartAccepted', { name: task.name }), 'success');
        await refreshControlPlaneData(task.serviceId, true);
      } catch (error) {
        handleApiActionError(error, tr('error.taskRestartFailed'));
      }
      return;
    }

    setTasks((prev) =>
      prev.map((t) => {
        if (t.id === taskId) {
          return { ...t, status: 'Idle' };
        }
        return t;
      })
    );
    addToast(tr('toast.taskRestartLocal', { id: taskId }), 'info');

    setTimeout(() => {
      setTasks((prev) =>
        prev.map((t) => {
          if (t.id === taskId) {
            return { ...t, status: 'Running' };
          }
          return t;
        })
      );
      addToast(tr('toast.taskRestartedLocal'), 'success');
    }, 1000);
  };

  // Save/Update Task Configuration
  const handleSaveConfig = (taskId: string, newConfigYaml: string) => {
    setTasks((prev) =>
      prev.map((t) => {
        if (t.id === taskId) {
          // Parse concurrency if modified
          let updatedConcurrency = t.concurrency;
          const match = newConfigYaml.match(/concurrency:\s*(\d+)/);
          if (match && match[1]) {
            updatedConcurrency = parseInt(match[1]);
          }

          addToast(tr('toast.configSaved', { name: t.name }), 'success');
          return {
            ...t,
            configYaml: newConfigYaml,
            concurrency: updatedConcurrency,
            status: 'Idle',
          };
        }
        return t;
      })
    );

    setTimeout(() => {
      setTasks((prev) =>
        prev.map((t) => {
          if (t.id === taskId) {
            return { ...t, status: 'Running' };
          }
          return t;
        })
      );
    }, 1000);
  };

  // Restart Instance
  const handleRestartInstance = async (uuid: string) => {
    const instance = instances.find((item) => item.uuid === uuid);
    if (instance?.apiServiceName) {
      setInstances((prev) =>
        prev.map((item) => (item.uuid === uuid ? { ...item, status: 'Starting' } : item))
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

    setInstances((prev) =>
      prev.map((inst) => {
        if (inst.uuid === uuid) {
          return { ...inst, status: 'Starting' };
        }
        return inst;
      })
    );
    addToast(tr('toast.instanceRebooting', { id: uuid.slice(0, 6) }), 'info');

    setTimeout(() => {
      setInstances((prev) =>
        prev.map((inst) => {
          if (inst.uuid === uuid) {
            return { ...inst, status: 'Running' };
          }
          return inst;
        })
      );
      addToast(tr('toast.instanceBackHealthy', { id: uuid.slice(0, 6) }), 'success');
    }, 1500);
  };

  // Toggle Instance (Stop / Start)
  const handleToggleInstance = async (uuid: string) => {
    const instance = instances.find((item) => item.uuid === uuid);
    if (instance?.apiServiceName) {
      if (instance.status !== 'Running') {
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

    setInstances((prev) =>
      prev.map((inst) => {
        if (inst.uuid === uuid) {
          const isRunning = inst.status === 'Running';
          const nextStatus = isRunning ? 'Stopped' : 'Running';
          addToast(tr('toast.instanceStatusChanged', { id: uuid.slice(0, 6), status: nextStatus.toUpperCase() }), isRunning ? 'warn' : 'success');
          return { ...inst, status: nextStatus };
        }
        return inst;
      })
    );
  };

  // Export spreadsheet triggers toast
  const handleExport = () => {
    addToast(tr('toast.exportStarted'), 'success');
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
        onViewChange={(view) => {
          setCurrentView(view);
          setSelectedTaskId(null);
        }}
        onSettingsClick={() => addToast(tr('toast.settingsReadOnly'), 'info')}
        onSupportClick={() => addToast(tr('toast.connectedSupport'), 'success')}
      />

      {/* --- Main Content Panel --- */}
      <div className="flex-1 flex flex-col ml-[240px] h-screen overflow-hidden">
        {/* --- Top Global Command Bar --- */}
        <TopAppBar
          services={services}
          selectedService={selectedService}
          onServiceChange={(svc) => {
            setSelectedServiceId(svc.id);
            setActiveTab('Tasks');
          }}
          activeTab={activeTab}
          setActiveTab={setActiveTab}
          isTaskView={!!selectedTaskId}
          onBackToService={() => setSelectedTaskId(null)}
          onNotificationsClick={() => {
            setCurrentView('notifications');
            setSelectedTaskId(null);
          }}
        />

        {/* --- Core Content Stage Canvas --- */}
        <main className="flex-1 overflow-y-auto p-6 bg-slate-50">
          <div className="max-w-7xl mx-auto mb-4 flex items-center justify-between rounded-lg border border-slate-200 bg-white px-4 py-2 text-xs font-semibold text-slate-600 shadow-xs">
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
            <button
              onClick={() => void refreshControlPlaneData(selectedServiceId)}
              disabled={isLoadingApi}
              className="flex items-center gap-1 rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] font-bold text-slate-600 transition-colors hover:bg-slate-100 disabled:opacity-50"
            >
              <RefreshCw className={`h-3 w-3 ${isLoadingApi ? 'animate-spin' : ''}`} />
              <span>{isLoadingApi ? tr('button.refreshing') : tr('button.refresh')}</span>
            </button>
          </div>

          {/* --- VIEW: Global Overview (if selected) --- */}
          {currentView === 'overview' && (
            <div className="max-w-7xl mx-auto space-y-6 animate-fadeIn">
              <div className="flex justify-between items-center">
                <div>
                  <h2 className="text-3xl font-bold text-slate-900 tracking-tight font-sans">
                    {tr('nav.globalOverview')}
                  </h2>
                  <p className="text-sm text-slate-500 font-medium">
                    {tr('overview.subtitle')}
                  </p>
                </div>
                <div className="flex items-center gap-2 text-xs font-bold text-slate-500">
                  <span className="w-2.5 h-2.5 rounded-full bg-emerald-500 animate-pulse" />
                  <span>{tr('overview.clusterSync')}</span>
                </div>
              </div>

              {/* Grid map cards */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                {[
                  { name: 'US-East-01 (Primary AWS)', health: '100%', delay: '4ms', location: 'Virginia, USA', code: 'aws' },
                  { name: 'GCP-West-02 (Secondary GCP)', health: '99.98%', delay: '12ms', location: 'Oregon, USA', code: 'gcp' },
                  { name: 'Azure-South-01 (Local Edge)', health: '98.5%', delay: '24ms', location: 'Frankfurt, GER', code: 'azure' },
                ].map((cluster, i) => (
                  <div key={i} className="bg-white border border-slate-200 rounded-xl p-5 hover:shadow-md transition-all">
                    <div className="flex justify-between items-center mb-3">
                      <span className="text-xs font-bold uppercase tracking-wide text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded">
                        {cluster.code}
                      </span>
                      <span className="text-xs font-bold text-emerald-600 flex items-center gap-1">
                        <CheckCircle className="w-3.5 h-3.5" /> {tr('common.healthy')}
                      </span>
                    </div>
                    <h3 className="font-bold text-slate-800 text-sm mb-1">{cluster.name}</h3>
                    <p className="text-xs text-slate-500 font-medium mb-3">{cluster.location}</p>
                    <div className="flex justify-between text-xs font-semibold text-slate-600 border-t border-slate-100 pt-3">
                      <span>{tr('common.uptimeValue', { value: cluster.health })}</span>
                      <span>{tr('common.ping', { delay: cluster.delay })}</span>
                    </div>
                  </div>
                ))}
              </div>

              {/* Services health metrics block */}
              <div className="bg-white border border-slate-200 rounded-xl p-6">
                <h3 className="font-bold text-slate-900 text-sm mb-4">{tr('overview.registry')}</h3>
                <div className="space-y-4">
                  {services.map((svc) => (
                    <div
                      key={svc.id}
                      onClick={() => {
                        setSelectedServiceId(svc.id);
                        setCurrentView('services');
                        setActiveTab('Tasks');
                      }}
                      className="flex items-center justify-between p-3.5 hover:bg-slate-50 rounded-lg cursor-pointer border border-transparent hover:border-slate-200 transition-all"
                    >
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded bg-indigo-50 flex items-center justify-center text-indigo-600">
                          <Database className="w-4 h-4" />
                        </div>
                        <div>
                          <h4 className="font-bold text-slate-800 text-sm">{svc.name}</h4>
                          <span className="text-xs text-slate-500 font-medium">{tr('common.uptimeValue', { value: svc.uptime })}</span>
                        </div>
                      </div>

                      <div className="flex gap-8 items-center text-xs font-bold text-slate-600">
                        <div>
                          <span className="text-[10px] text-slate-400 block mb-0.5">{tr('common.throughput')}</span>
                          <span>{svc.throughput}</span>
                        </div>
                        <div>
                          <span className="text-[10px] text-slate-400 block mb-0.5">{tr('common.instances')}</span>
                          <span>{svc.activeInstances}/{svc.totalInstances} {tr('common.active')}</span>
                        </div>
                        <div>
                          <span className="text-[10px] text-slate-400 block mb-0.5">{tr('common.health')}</span>
                          <span className="text-emerald-600">{svc.taskHealth}%</span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {currentView === 'notifications' && (
            <NotificationSettingsPage
              onAuthRequired={redirectToLogin}
              onNotify={addToast}
            />
          )}

          {/* --- VIEW: Services Detail (Default) --- */}
          {currentView === 'services' && (
            <div className="max-w-7xl mx-auto space-y-6">
              {/* Breadcrumb navigator under Command Bar */}
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 animate-fadeIn">
                <div>
                  <nav aria-label="Breadcrumb" className="flex items-center text-slate-400 font-medium text-xs mb-1">
                    <button
                      onClick={() => setSelectedTaskId(null)}
                      className="hover:text-indigo-600 transition-colors"
                    >
                      {tr('nav.services')}
                    </button>
                    <ChevronRight className="w-3.5 h-3.5 mx-1" />
                    <span className="text-slate-800 font-bold">{selectedService.name.split(' ')[0]}</span>
                    {selectedTask && (
                      <>
                        <ChevronRight className="w-3.5 h-3.5 mx-1" />
                        <span className="text-slate-500 font-semibold">{tr('tabs.tasks')}</span>
                        <ChevronRight className="w-3.5 h-3.5 mx-1" />
                        <span className="text-slate-900 font-bold">{selectedTask.name}</span>
                      </>
                    )}
                  </nav>

                  <div className="flex items-center gap-3 mt-1.5">
                    <h2 className="text-2xl font-extrabold text-slate-900 tracking-tight font-sans">
                      {selectedTask ? selectedTask.name : tr('service.detail')}
                    </h2>
                    <span className="bg-emerald-50 text-emerald-700 px-2 py-0.5 rounded-md font-bold tracking-wide text-[10px] flex items-center gap-1 border border-emerald-200">
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                      {tr('common.running')}
                    </span>
                  </div>
                </div>

                {/* Main Action Triggers */}
                <div className="flex gap-2 self-start sm:self-center">
                  {selectedTask ? (
                    <>
                      <button
                        onClick={() => handleRestartTask(selectedTask.id)}
                        className="flex items-center gap-1.5 px-4 py-2 border border-slate-200 rounded-lg text-indigo-600 hover:bg-slate-100 transition-colors text-xs font-bold bg-white shadow-xs"
                      >
                        <RotateCcw className="w-4 h-4" />
                        <span>{tr('button.restart')}</span>
                      </button>
                      <button
                        onClick={() => handleToggleTaskStatus(selectedTask.id)}
                        className={`flex items-center gap-1.5 px-4 py-2 border border-slate-200 rounded-lg transition-colors text-xs font-bold bg-white shadow-xs ${
                          selectedTask.status === 'Running'
                            ? 'text-rose-600 hover:bg-rose-50 border-rose-200'
                            : 'text-emerald-600 hover:bg-emerald-50 border-emerald-200'
                        }`}
                      >
                        {selectedTask.status === 'Running' ? (
                          <>
                            <Square className="w-4 h-4" />
                            <span>{tr('button.stopTask')}</span>
                          </>
                        ) : (
                          <>
                            <Play className="w-4 h-4" />
                            <span>{tr('button.startTask')}</span>
                          </>
                        )}
                      </button>
                      <button
                        onClick={() => {
                          setSelectedTaskId(null);
                          setActiveTab('Configuration');
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
                        disabled={isRestartingAll}
                        className="flex items-center gap-1.5 px-4 py-2 border border-slate-200 rounded-lg text-slate-700 hover:bg-slate-100 transition-colors text-xs font-bold bg-white shadow-xs disabled:opacity-50"
                      >
                        <RefreshCw className={`w-4 h-4 ${isRestartingAll ? 'animate-spin' : ''}`} />
                        <span>{isRestartingAll ? tr('button.restarting') : tr('button.restartAll')}</span>
                      </button>
                      <button
                        onClick={handleDeployUpdate}
                        disabled={isDeploying}
                        className="flex items-center gap-1.5 px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-800 transition-colors text-xs font-bold shadow-xs disabled:opacity-50"
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
                      <div className="text-2xl font-bold text-slate-900 mt-2">{selectedTask.uptime}</div>
                      <span className="text-[10px] text-slate-500 font-medium">{tr('task.activeStreamStatus')}</span>
                    </div>

                    {/* Stat: Throughput */}
                    <div className="bg-white border border-slate-200 rounded-xl p-5 flex flex-col justify-between h-28 shadow-xs">
                      <span className="text-[10px] text-slate-400 uppercase font-bold tracking-wider block">
                        {tr('common.throughput')}
                      </span>
                      <div className="text-2xl font-bold text-slate-900 mt-2">
                        {selectedTask.throughputValue.split(' ')[0]}{' '}
                        <span className="text-xs text-slate-500 font-normal">
                          {selectedTask.throughputValue.substring(selectedTask.throughputValue.indexOf(' ') + 1)}
                        </span>
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
                        {tr('task.errorCount24h')}
                      </span>
                      <div className="text-2xl font-bold text-rose-600 mt-2">{selectedTask.errorCount}</div>
                      <button
                        onClick={() => setShowTraces(!showTraces)}
                        className="text-[10px] text-indigo-600 hover:underline font-bold text-left block"
                      >
                        {showTraces ? tr('task.closeTraceView') : tr('task.viewTraces')}
                      </button>
                    </div>
                  </div>

                  {/* Traces Slider Tray */}
                  {showTraces && (
                    <div className="bg-[#1e2230] text-slate-200 border border-slate-700 p-5 rounded-xl animate-slideDown font-sans">
                      <div className="flex justify-between items-center border-b border-slate-700/60 pb-3 mb-3">
                        <div className="flex items-center gap-2">
                          <History className="text-indigo-600 w-4 h-4 animate-spin" />
                          <h4 className="font-bold text-xs text-white">{tr('task.traces')}</h4>
                        </div>
                        <span className="text-[10px] text-slate-400 font-medium">{tr('task.traceCloseHint')}</span>
                      </div>
                      <div className="space-y-2 max-h-48 overflow-y-auto">
                        {MOCK_TRACES.map((trace) => (
                          <div
                            key={trace.id}
                            className="p-2.5 bg-[#161a25]/60 hover:bg-[#161a25] rounded-md border border-slate-700/50 flex justify-between items-center text-xs font-mono"
                          >
                            <div className="flex items-center gap-4">
                              <span className="text-[#e5c07b] font-bold">{trace.id}</span>
                              <span className="text-slate-400 font-semibold">{trace.path}</span>
                            </div>
                            <div className="flex items-center gap-4">
                              <span className="text-slate-400">{trace.timestamp.slice(11, 19)}</span>
                              <span className="text-slate-400">{trace.duration}</span>
                              <span
                                className={`px-1.5 py-0.5 rounded font-bold text-[10px] ${
                                  trace.status === 200
                                    ? 'bg-emerald-500/10 text-emerald-400'
                                    : 'bg-rose-500/10 text-rose-400'
                                }`}
                              >
                                {trace.status} {trace.error ? `(${trace.error})` : ''}
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Main dual column for Task Details */}
                  <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                    {/* Left 2/3 column: Topology + Resource chart */}
                    <div className="lg:col-span-2 space-y-6">
                      <TopologyFlow task={selectedTask} />
                      <ResourceChart />
                    </div>

                    {/* Right 1/3 column: Config editor */}
                    <div className="lg:col-span-1">
                      <ConfigEditor task={selectedTask} onSaveConfig={handleSaveConfig} />
                    </div>
                  </div>
                </div>
              ) : (
                /* --- STAGE: Service tabs (Instances / Tasks / Config / Logs) --- */
                <div className="space-y-6">
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
                        onTaskSelect={(task) => setSelectedTaskId(task.id)}
                        onRestartTask={handleRestartTask}
                        onToggleTaskStatus={handleToggleTaskStatus}
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
                        onExport={handleExport}
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

                      <div className="bg-[#1a1b26] p-4 rounded-lg font-mono text-xs text-slate-300 space-y-1">
                        <div>
                          <span className="text-rose-400">service_name</span>:{' '}
                          <span className="text-emerald-400">"{selectedService.id}"</span>
                        </div>
                        <div>
                          <span className="text-rose-400">env</span>: <span className="text-emerald-400">"production"</span>
                        </div>
                        <div>
                          <span className="text-rose-400">replication_factor</span>:{' '}
                          <span className="text-amber-400">3</span>
                        </div>
                        <div>
                          <span className="text-rose-400">failover_threshold</span>:{' '}
                          <span className="text-amber-400">0.95</span>
                        </div>
                        <div>
                          <span className="text-rose-400">tls_encryption</span>: <span className="text-amber-400">true</span>
                        </div>
                      </div>

                      <button
                        onClick={() => addToast(tr('toast.globalConfigReadOnly'), 'warn')}
                        className="px-4 py-2 bg-indigo-600 text-white hover:bg-indigo-800 text-xs font-bold rounded-lg transition-colors"
                      >
                        {tr('button.modifyGlobalConfig')}
                      </button>
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
                          onClick={() => setLogs(INITIAL_LOGS)}
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
                        <span>{tr('logs.socketConnected')}</span>
                        <span>{tr('logs.streamingAlive')}</span>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
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
