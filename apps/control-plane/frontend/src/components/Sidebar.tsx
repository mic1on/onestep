import { Bell, LayoutDashboard, Server } from 'lucide-react';
import { useI18n } from '../i18n';
import type { ControlPlaneView } from '../appRoute';

export type { ControlPlaneView };

interface SidebarProps {
  currentView: ControlPlaneView;
  onViewChange: (view: ControlPlaneView) => void;
}

export default function Sidebar({
  currentView,
  onViewChange,
}: SidebarProps) {
  const { t } = useI18n();

  const isServicesActive = currentView === 'servicesList' || currentView === 'services';

  return (
    <nav className="bg-white border-r border-slate-200 fixed left-0 top-0 h-full w-[240px] flex flex-col p-4 z-20 shadow-xs">
      <div className="mb-8">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-indigo-600 flex items-center justify-center text-white">
            <Server className="w-5 h-5" />
          </div>
          <div>
            <h1 className="font-sans text-lg font-bold text-slate-900 tracking-tight">OneStep</h1>
            <p className="font-sans text-xs text-slate-500 font-medium">{t('common.controlPlane')} v2.4.0</p>
          </div>
        </div>
      </div>

      <ul className="flex-1 space-y-1">
        <li>
          <button
            onClick={() => onViewChange('overview')}
            className={`w-full flex items-center gap-3 p-2.5 text-sm transition-colors rounded-lg font-medium ${
              currentView === 'overview'
                ? 'bg-slate-100 text-slate-900 font-semibold'
                : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
            }`}
          >
            <LayoutDashboard className="w-4 h-4 text-slate-500" />
            <span>{t('nav.globalOverview')}</span>
          </button>
        </li>
        <li>
          <button
            onClick={() => onViewChange('servicesList')}
            className={`w-full flex items-center gap-3 p-2.5 text-sm transition-colors rounded-lg font-medium ${
              isServicesActive
                ? 'bg-indigo-50 text-indigo-700 font-bold'
                : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
            }`}
          >
            <Server className={`w-4 h-4 ${isServicesActive ? 'text-indigo-600' : 'text-slate-500'}`} />
            <span>{t('nav.services')}</span>
          </button>
        </li>
        <li>
          <button
            onClick={() => onViewChange('notifications')}
            className={`w-full flex items-center gap-3 p-2.5 text-sm transition-colors rounded-lg font-medium ${
              currentView === 'notifications'
                ? 'bg-indigo-50 text-indigo-700 font-bold'
                : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
            }`}
          >
            <Bell className={`w-4 h-4 ${currentView === 'notifications' ? 'text-indigo-600' : 'text-slate-500'}`} />
            <span>{t('nav.notifications')}</span>
          </button>
        </li>
      </ul>
    </nav>
  );
}
