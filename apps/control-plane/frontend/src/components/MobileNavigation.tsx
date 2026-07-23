import { useCallback, useState } from 'react';
import { Bell, BookOpen, Github, LayoutDashboard, LogOut, MoreHorizontal, Server, X } from 'lucide-react';
import type { ControlPlaneView } from '../appRoute';
import { useI18n } from '../i18n';
import LocaleSwitcher from './LocaleSwitcher';
import useDismissibleMenu from './useDismissibleMenu';

interface MobileNavigationProps {
  currentView: ControlPlaneView;
  isLogoutPending?: boolean;
  onLogout: () => void;
  onViewChange: (view: ControlPlaneView) => void;
}

export default function MobileNavigation({
  currentView,
  isLogoutPending = false,
  onLogout,
  onViewChange,
}: MobileNavigationProps) {
  const { t } = useI18n();
  const [isMoreOpen, setIsMoreOpen] = useState(false);
  const closeMore = useCallback(() => setIsMoreOpen(false), []);
  const { menuRef, triggerRef } = useDismissibleMenu({
    onClose: closeMore,
    open: isMoreOpen,
    trapFocus: true,
  });
  const servicesActive = currentView === 'servicesList' || currentView === 'services';

  const navigate = (view: ControlPlaneView) => {
    closeMore();
    onViewChange(view);
  };

  return (
    <>
      <nav
        aria-label={t('common.controlPlane')}
        className="fixed inset-x-0 bottom-0 z-30 grid h-[calc(4.5rem+env(safe-area-inset-bottom))] grid-cols-4 border-t border-slate-200 bg-white/95 px-2 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden"
      >
        <button aria-current={currentView === 'overview' ? 'page' : undefined} className="ui-mobile-nav-item" onClick={() => navigate('overview')} type="button">
          <LayoutDashboard className="h-5 w-5" />
          <span>{t('nav.globalOverview')}</span>
        </button>
        <button aria-current={servicesActive ? 'page' : undefined} className="ui-mobile-nav-item" onClick={() => navigate('servicesList')} type="button">
          <Server className="h-5 w-5" />
          <span>{t('nav.services')}</span>
        </button>
        <button aria-current={currentView === 'notifications' ? 'page' : undefined} className="ui-mobile-nav-item" onClick={() => navigate('notifications')} type="button">
          <Bell className="h-5 w-5" />
          <span>{t('nav.notifications')}</span>
        </button>
        <button ref={triggerRef} aria-expanded={isMoreOpen} aria-haspopup="dialog" aria-label={t('nav.moreMenu')} className="ui-mobile-nav-item" onClick={() => setIsMoreOpen((open) => !open)} type="button">
          <MoreHorizontal className="h-5 w-5" />
          <span>{t('nav.more')}</span>
        </button>
      </nav>

      {isMoreOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button aria-label={t('nav.closeMoreMenu')} className="absolute inset-0 bg-slate-950/30" onClick={closeMore} type="button" />
          <section ref={menuRef} aria-label={t('nav.more')} aria-modal="true" className="ui-dialog-enter absolute inset-x-0 bottom-0 rounded-t-xl bg-white px-4 pb-[calc(1rem+env(safe-area-inset-bottom))] pt-3 shadow-2xl" role="dialog">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-bold text-slate-900">{t('nav.more')}</h2>
              <button aria-label={t('nav.closeMoreMenu')} className="ui-pressable grid h-11 w-11 place-items-center rounded-md text-slate-500 hover:bg-slate-100" onClick={closeMore} type="button"><X className="h-5 w-5" /></button>
            </div>
            <div className="flex items-center justify-between border-y border-slate-100 py-2"><span className="text-sm font-semibold text-slate-700">Language</span><LocaleSwitcher /></div>
            <a className="ui-pressable flex min-h-11 items-center gap-3 border-b border-slate-100 py-3 text-sm font-semibold text-slate-700" href="https://onestep.code05.com/" rel="noreferrer" target="_blank"><BookOpen className="h-5 w-5 text-slate-500" />{t('nav.docs')}</a>
            <a className="ui-pressable flex min-h-11 items-center gap-3 border-b border-slate-100 py-3 text-sm font-semibold text-slate-700" href="https://github.com/mic1on/onestep" rel="noreferrer" target="_blank"><Github className="h-5 w-5 text-slate-500" />{t('nav.github')}</a>
            <button aria-busy={isLogoutPending} className="ui-pressable flex min-h-11 w-full items-center gap-3 py-3 text-left text-sm font-semibold text-rose-600 disabled:opacity-50" disabled={isLogoutPending} onClick={() => { closeMore(); onLogout(); }} type="button"><LogOut className="h-5 w-5" />{isLogoutPending ? t('button.signingOut') : t('button.signOut')}</button>
          </section>
        </div>
      )}
    </>
  );
}
