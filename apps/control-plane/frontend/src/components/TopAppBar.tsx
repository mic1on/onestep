import { Bell, ChevronDown } from 'lucide-react';
import { Service } from '../types';
import { useI18n } from '../i18n';

type RuntimeEnvironment = 'prod' | 'dev';

interface TopAppBarProps {
  services: Service[];
  selectedService: Service;
  onServiceChange: (service: Service) => void;
  activeTab: 'Tasks' | 'Instances' | 'Configuration' | 'Logs';
  setActiveTab: (tab: 'Tasks' | 'Instances' | 'Configuration' | 'Logs') => void;
  isTaskView: boolean;
  onBackToService: () => void;
  onNotificationsClick: () => void;
}

export default function TopAppBar({
  services,
  selectedService,
  onServiceChange,
  activeTab,
  setActiveTab,
  isTaskView,
  onBackToService,
  onNotificationsClick,
}: TopAppBarProps) {
  const { t } = useI18n();
  const tabLabels: Record<TopAppBarProps['activeTab'], string> = {
    Tasks: t('tabs.tasks'),
    Instances: t('tabs.instances'),
    Configuration: t('tabs.configuration'),
    Logs: t('tabs.logs'),
  };
  const environmentOptions: Array<{ value: RuntimeEnvironment; label: string }> = [
    { value: 'prod', label: t('top.production') },
    { value: 'dev', label: t('top.development') },
  ];
  const selectedEnvironment = getServiceEnvironment(selectedService);
  const selectedServiceName = getServiceName(selectedService);

  function findServiceForEnvironment(environment: RuntimeEnvironment) {
    return (
      services.find(
        (service) =>
          getServiceEnvironment(service) === environment &&
          getServiceName(service) === selectedServiceName,
      ) ?? null
    );
  }

  return (
    <header className="bg-white border-b border-slate-200 flex justify-between items-center w-full px-6 h-16 shrink-0 z-10 shadow-xs">
      <div className="flex items-center gap-6 h-full">
        <div className="flex items-center gap-2">
          <span className="font-sans text-xs uppercase font-bold tracking-wider text-slate-400">{t('top.context')}</span>
          <div className="relative group">
            <button className="flex items-center gap-2 text-md font-semibold text-indigo-600 hover:text-indigo-800 transition-colors py-1.5 px-2.5 rounded-lg hover:bg-slate-50">
              <span>{environmentLabel(selectedEnvironment, t)}</span>
              <ChevronDown className="w-4 h-4 text-slate-400" />
            </button>
            <div className="absolute left-0 mt-1 w-64 bg-white border border-slate-200 rounded-lg shadow-lg opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto transition-all z-50">
              <div className="p-2 border-b border-slate-100 bg-slate-50 rounded-t-lg">
                <span className="text-xs font-semibold text-slate-500 px-2 block">{t('top.switchEnvironment')}</span>
              </div>
              <ul className="p-1 space-y-0.5">
                {environmentOptions.map((environment) => {
                  const targetService = findServiceForEnvironment(environment.value);
                  const active = selectedEnvironment === environment.value;
                  return (
                    <li key={environment.value}>
                      <button
                        disabled={!targetService}
                        onClick={() => {
                          if (!targetService) return;
                          onServiceChange(targetService);
                          if (isTaskView) onBackToService();
                        }}
                        className={`w-full text-left px-3 py-2 rounded-md text-sm transition-colors flex justify-between items-center ${
                          active
                            ? 'bg-indigo-50 text-indigo-700 font-semibold'
                            : targetService
                            ? 'text-slate-700 hover:bg-slate-50'
                            : 'text-slate-400 cursor-not-allowed'
                        }`}
                      >
                        <span>{environment.label}</span>
                        <span className={`w-2 h-2 rounded-full ${active ? 'bg-indigo-500' : 'bg-slate-300'}`} />
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          </div>
        </div>

        {/* Navigation Tabs - Hide or style differently if in task view, or let them switch pages */}
        <nav className="ml-8 h-full flex items-end">
          <ul className="flex gap-6 h-full">
            {(['Tasks', 'Instances', 'Configuration', 'Logs'] as const).map((tab) => {
              const isActive = activeTab === tab && !isTaskView;
              return (
                <li key={tab} className="h-full flex items-end">
                  <button
                    onClick={() => {
                      setActiveTab(tab);
                      if (isTaskView) {
                        onBackToService();
                      }
                    }}
                    className={`pb-4 px-1 text-sm font-semibold transition-colors border-b-2 relative ${
                      isActive
                        ? 'border-indigo-600 text-indigo-600'
                        : 'border-transparent text-slate-500 hover:text-slate-800 hover:border-slate-300'
                    }`}
                  >
                    {tabLabels[tab]}
                    {tab === 'Logs' && (
                      <span className="absolute -top-0.5 -right-3 flex h-2 w-2">
                        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-indigo-400 opacity-75"></span>
                        <span className="relative inline-flex rounded-full h-2 w-2 bg-indigo-500"></span>
                      </span>
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        </nav>
      </div>

      <div className="flex items-center gap-3">
        {/* Status Pills */}
        <div className="hidden md:flex items-center gap-1.5 text-xs font-semibold bg-emerald-50 text-emerald-700 px-2.5 py-1 rounded-full border border-emerald-200">
          <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
          <span>{t('top.systemNormal')}</span>
        </div>

        <button
          onClick={onNotificationsClick}
          className="relative flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-bold text-slate-600 transition-colors hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-700"
          title={t('top.systemAlerts')}
        >
          <Bell className="w-4 h-4" />
          <span>{t('nav.notifications')}</span>
          <span className="absolute -right-0.5 -top-0.5 w-2 h-2 rounded-full bg-rose-500" />
        </button>

        <div className="w-9 h-9 rounded-full bg-indigo-100 flex items-center justify-center text-indigo-700 font-bold text-sm ring-2 ring-transparent hover:ring-indigo-100 transition-all cursor-pointer">
          JD
        </div>
      </div>
    </header>
  );
}

function getServiceEnvironment(service: Service): RuntimeEnvironment {
  if (service.environment === 'prod' || service.environment === 'dev') return service.environment;
  return /\bdev(elopment)?\b/i.test(service.name) ? 'dev' : 'prod';
}

function getServiceName(service: Service) {
  return (
    service.apiName ??
    service.name
      .replace(/\s*\/\s*(prod|production|dev|development|staging)$/i, '')
      .replace(/\s*\((prod|production|dev|development|staging)\)$/i, '')
  );
}

function environmentLabel(
  environment: RuntimeEnvironment,
  t: ReturnType<typeof useI18n>['t'],
) {
  return environment === 'prod' ? t('top.production') : t('top.development');
}
