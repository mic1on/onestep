import { Bell, BookOpen, Github, LayoutDashboard, LogOut, Server } from 'lucide-react';
import { useI18n } from '../i18n';
import type { ControlPlaneView } from '../appRoute';
import BrandLogo from './BrandLogo';

export type { ControlPlaneView };

interface SidebarProps {
  currentView: ControlPlaneView;
  isLogoutPending?: boolean;
  onLogout: () => void;
  onViewChange: (view: ControlPlaneView) => void;
}

export default function Sidebar({
  currentView,
  isLogoutPending = false,
  onLogout,
  onViewChange,
}: SidebarProps) {
  const { t } = useI18n();

  const isServicesActive = currentView === 'servicesList' || currentView === 'services';

  return (
    <nav className="bg-white border-r border-slate-200 fixed left-0 top-0 h-full w-[240px] flex flex-col p-4 z-20 shadow-xs">
      <div className="mb-8">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-indigo-50 flex items-center justify-center text-indigo-600">
            <BrandLogo className="h-8 w-7" decorative />
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

      <div className="border-t border-slate-200 pt-3">
        <a
          className="flex w-full items-center gap-3 rounded-lg p-2.5 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-50 hover:text-slate-900"
          href="https://onestep.code05.com/"
          rel="noreferrer"
          target="_blank"
        >
          <BookOpen className="h-4 w-4 text-slate-500" />
          <span>{t('nav.docs')}</span>
        </a>
        <a
          className="flex w-full items-center gap-3 rounded-lg p-2.5 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-50 hover:text-slate-900"
          href="https://github.com/mic1on/onestep"
          rel="noreferrer"
          target="_blank"
        >
          <Github className="h-4 w-4 text-slate-500" />
          <span>{t('nav.github')}</span>
        </a>
        <button
          className="flex w-full items-center gap-3 rounded-lg p-2.5 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-60"
          disabled={isLogoutPending}
          onClick={onLogout}
          type="button"
        >
          <LogOut className="h-4 w-4 text-slate-500" />
          <span>{isLogoutPending ? t('button.signingOut') : t('button.signOut')}</span>
        </button>
      </div>
    </nav>
  );
}
