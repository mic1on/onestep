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
    <nav className="fixed left-0 top-0 z-20 hidden h-full w-[240px] flex-col border-r border-slate-200 bg-white p-4 shadow-xs lg:flex">
      <div className="mb-8">
        <div className="flex items-center justify-center gap-3 sm:justify-start">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600">
            <BrandLogo className="h-8 w-7" decorative />
          </div>
          <div className="hidden sm:block">
            <h1 className="font-sans text-lg font-bold text-slate-900 tracking-tight">OneStep</h1>
            <p className="font-sans text-xs text-slate-500 font-medium">{t('common.controlPlane')} v2.4.0</p>
          </div>
        </div>
      </div>

      <ul className="flex-1 space-y-1">
        <li>
          <button
            aria-label={t('nav.globalOverview')}
            onClick={() => onViewChange('overview')}
            className={`ui-pressable flex w-full items-center justify-center rounded-lg p-2.5 text-sm font-medium sm:justify-start sm:gap-3 ${
              currentView === 'overview'
                ? 'bg-slate-100 text-slate-900 font-semibold'
                : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
            }`}
          >
            <LayoutDashboard className="w-4 h-4 text-slate-500" />
            <span className="hidden sm:inline">{t('nav.globalOverview')}</span>
          </button>
        </li>
        <li>
          <button
            aria-label={t('nav.services')}
            onClick={() => onViewChange('servicesList')}
            className={`ui-pressable flex w-full items-center justify-center rounded-lg p-2.5 text-sm font-medium sm:justify-start sm:gap-3 ${
              isServicesActive
                ? 'bg-indigo-50 text-indigo-700 font-bold'
                : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
            }`}
          >
            <Server className={`w-4 h-4 ${isServicesActive ? 'text-indigo-600' : 'text-slate-500'}`} />
            <span className="hidden sm:inline">{t('nav.services')}</span>
          </button>
        </li>
        <li>
          <button
            aria-label={t('nav.notifications')}
            onClick={() => onViewChange('notifications')}
            className={`ui-pressable flex w-full items-center justify-center rounded-lg p-2.5 text-sm font-medium sm:justify-start sm:gap-3 ${
              currentView === 'notifications'
                ? 'bg-indigo-50 text-indigo-700 font-bold'
                : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'
            }`}
          >
            <Bell className={`w-4 h-4 ${currentView === 'notifications' ? 'text-indigo-600' : 'text-slate-500'}`} />
            <span className="hidden sm:inline">{t('nav.notifications')}</span>
          </button>
        </li>
      </ul>

      <div className="border-t border-slate-200 pt-3">
        <a
          aria-label={t('nav.docs')}
          className="ui-pressable flex w-full items-center justify-center rounded-lg p-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50 hover:text-slate-900 sm:justify-start sm:gap-3"
          href="https://onestep.code05.com/"
          rel="noreferrer"
          target="_blank"
        >
          <BookOpen className="h-4 w-4 text-slate-500" />
          <span className="hidden sm:inline">{t('nav.docs')}</span>
        </a>
        <a
          aria-label={t('nav.github')}
          className="ui-pressable flex w-full items-center justify-center rounded-lg p-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50 hover:text-slate-900 sm:justify-start sm:gap-3"
          href="https://github.com/mic1on/onestep"
          rel="noreferrer"
          target="_blank"
        >
          <Github className="h-4 w-4 text-slate-500" />
          <span className="hidden sm:inline">{t('nav.github')}</span>
        </a>
        <button
          aria-label={isLogoutPending ? t('button.signingOut') : t('button.signOut')}
          aria-busy={isLogoutPending}
          className="ui-pressable flex w-full items-center justify-center rounded-lg p-2.5 text-sm font-medium text-slate-600 hover:bg-slate-50 hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-60 sm:justify-start sm:gap-3"
          disabled={isLogoutPending}
          onClick={onLogout}
          type="button"
        >
          <LogOut className="h-4 w-4 text-slate-500" />
          <span className="hidden sm:inline">{isLogoutPending ? t('button.signingOut') : t('button.signOut')}</span>
        </button>
      </div>
    </nav>
  );
}
