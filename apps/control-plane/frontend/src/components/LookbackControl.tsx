import { useState, type FormEvent } from 'react';
import { useI18n } from '../i18n';

interface LookbackControlProps {
  ariaLabel: string;
  lookbackMinutes: number;
  maxLookbackMinutes: number;
  presets: readonly number[];
  onLookbackMinutesChange: (minutes: number) => void;
}

export default function LookbackControl({
  ariaLabel,
  lookbackMinutes,
  maxLookbackMinutes,
  presets,
  onLookbackMinutesChange,
}: LookbackControlProps) {
  const { t } = useI18n();
  const [isCustomLookbackOpen, setIsCustomLookbackOpen] = useState(false);
  const [customLookbackValue, setCustomLookbackValue] = useState(String(lookbackMinutes));

  const applyCustomLookback = () => {
    const value = Number(customLookbackValue);
    if (!Number.isFinite(value)) {
      setCustomLookbackValue(String(lookbackMinutes));
      return;
    }

    const next = Math.min(maxLookbackMinutes, Math.max(1, Math.trunc(value)));
    setCustomLookbackValue(String(next));
    if (next !== lookbackMinutes) {
      onLookbackMinutesChange(next);
    }
  };

  const applyPresetLookback = (minutes: number) => {
    setIsCustomLookbackOpen(false);
    if (minutes !== lookbackMinutes) {
      onLookbackMinutesChange(minutes);
    }
  };

  const handleCustomLookbackSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    applyCustomLookback();
  };

  return (
    <div className="flex flex-wrap items-center gap-2 text-[10px] font-bold">
      <div
        role="group"
        aria-label={ariaLabel}
        className="flex items-center gap-1 rounded-md border border-slate-200 bg-white p-1"
      >
        {presets.map((minutes) => (
          <button
            key={minutes}
            type="button"
            aria-pressed={!isCustomLookbackOpen && lookbackMinutes === minutes}
            onClick={() => applyPresetLookback(minutes)}
            className={`h-6 rounded px-2 transition-colors ${
              !isCustomLookbackOpen && lookbackMinutes === minutes
                ? 'bg-indigo-50 text-indigo-600'
                : 'text-slate-500 hover:bg-slate-50 hover:text-slate-700'
            }`}
          >
            {minutes}m
          </button>
        ))}
        <button
          type="button"
          aria-pressed={isCustomLookbackOpen}
          onClick={() => setIsCustomLookbackOpen(true)}
          className={`h-6 rounded px-2 transition-colors ${
            isCustomLookbackOpen
              ? 'bg-indigo-50 text-indigo-600'
              : 'text-slate-500 hover:bg-slate-50 hover:text-slate-700'
          }`}
        >
          {t('chart.customLookback')}
        </button>
      </div>

      {isCustomLookbackOpen && (
        <form onSubmit={handleCustomLookbackSubmit} className="flex items-center gap-1.5">
          <input
            aria-label={t('chart.lookbackMinutes')}
            type="number"
            min={1}
            max={maxLookbackMinutes}
            value={customLookbackValue}
            onChange={(event) => setCustomLookbackValue(event.target.value)}
            className="h-7 w-14 rounded-md border border-slate-200 bg-white px-2 text-right font-mono text-slate-700 outline-none transition focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
          />
          <span className="text-slate-400">{t('chart.minutesUnit')}</span>
          <button
            type="submit"
            className="h-7 rounded-md border border-slate-200 bg-white px-2 text-slate-600 transition-colors hover:bg-slate-100"
          >
            {t('chart.applyLookback')}
          </button>
        </form>
      )}
    </div>
  );
}
