import { useCallback, useState } from 'react';
import { Check, Globe } from 'lucide-react';
import { useI18n, type Locale } from '../i18n';
import useDismissibleMenu from './useDismissibleMenu';

// Native names are shown as each language refers to itself — standard practice
// for locale pickers, so a user who doesn't read the current UI can still find
// their language.
const LOCALE_OPTIONS: Array<{ value: Locale; label: string; short: string }> = [
  { value: 'en', label: 'English', short: 'EN' },
  { value: 'zh-CN', label: '中文', short: '中' },
];

export default function LocaleSwitcher() {
  const { locale, setLocale } = useI18n();
  const [isOpen, setIsOpen] = useState(false);
  const closeMenu = useCallback(() => setIsOpen(false), []);
  const { menuRef, triggerRef } = useDismissibleMenu({ onClose: closeMenu, open: isOpen });
  const current = LOCALE_OPTIONS.find((option) => option.value === locale) ?? LOCALE_OPTIONS[0];

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        onClick={() => setIsOpen((open) => !open)}
        className="ui-pressable flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-bold text-slate-600 hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-700"
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        title="Language"
        type="button"
      >
        <Globe className="w-4 h-4" />
        <span>{current.short}</span>
      </button>
      {isOpen && (
          <ul
            ref={menuRef}
            role="listbox"
            className="ui-popover-enter absolute right-0 z-40 mt-1 w-40 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 text-xs font-medium shadow-lg"
          >
            {LOCALE_OPTIONS.map((option) => {
              const active = option.value === locale;
              return (
                <li key={option.value}>
                  <button
                    role="option"
                    aria-selected={active}
                    onClick={() => {
                      setLocale(option.value);
                      closeMenu();
                      triggerRef.current?.focus();
                    }}
                    className={`ui-pressable flex w-full items-center justify-between px-3 py-1.5 text-left ${
                      active
                        ? 'bg-indigo-50 font-bold text-indigo-700'
                        : 'text-slate-700 hover:bg-slate-50'
                    }`}
                  >
                    <span>{option.label}</span>
                    {active && <Check className="h-3 w-3 text-indigo-600" />}
                  </button>
                </li>
              );
            })}
          </ul>
      )}
    </div>
  );
}
