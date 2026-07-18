import { useState, useEffect } from 'react';
import { Terminal, Copy, Check } from 'lucide-react';
import { Task } from '../types';
import { useI18n } from '../i18n';

interface ConfigEditorProps {
  task: Task;
}

/**
 * Read-only view of the task's reported YAML configuration.
 *
 * The control plane only mirrors whatever the runtime worker reports (see
 * `reporter._build_connector_config`); there is no backend endpoint that
 * accepts config edits, and the runtime has no command to hot-reload a task
 * definition. Editing here would only mutate browser memory and silently
 * revert on refresh, so this component renders the config strictly read-only
 * with a copy button as the only affordance.
 */
export default function ConfigEditor({ task }: ConfigEditorProps) {
  const { t } = useI18n();
  const [isCopied, setIsCopied] = useState(false);

  const yamlContent = task.configYaml;

  useEffect(() => {
    setIsCopied(false);
  }, [task.configYaml]);

  const handleCopy = () => {
    navigator.clipboard.writeText(yamlContent);
    setIsCopied(true);
    setTimeout(() => setIsCopied(false), 2000);
  };

  // Basic syntax highlighter for rendering
  const renderHighlightedYaml = (code: string) => {
    const lines = code.split('\n');
    return lines.map((line, idx) => {
      // Find comment
      const commentIdx = line.indexOf('#');
      let mainContent = line;
      let comment = '';
      if (commentIdx !== -1) {
        mainContent = line.substring(0, commentIdx);
        comment = line.substring(commentIdx);
      }

      // Check for key-value structures
      const colonIdx = mainContent.indexOf(':');
      if (colonIdx !== -1) {
        const key = mainContent.substring(0, colonIdx);
        const val = mainContent.substring(colonIdx + 1);

        const isMainHeader = key.trim() === 'task_config';

        return (
          <div key={idx} className="font-mono text-xs leading-relaxed flex">
            <span className="text-slate-500/80 select-none pr-3 text-right w-8 text-[10px]">
              {idx + 1}
            </span>
            <span>
              {/* Render key */}
              <span className={isMainHeader ? 'text-[#e5c07b] font-bold' : 'text-[#ef596f]'}>
                {key}
              </span>
              <span className="text-[#abb2bf]">:</span>
              {/* Render value */}
              {val.trim().startsWith('"') ? (
                <span className="text-[#98c379]">{val}</span>
              ) : (
                <span className="text-[#d19a66]">{val}</span>
              )}
              {comment && <span className="text-slate-500/90 italic">{comment}</span>}
            </span>
          </div>
        );
      }

      return (
        <div key={idx} className="font-mono text-xs leading-relaxed flex">
          <span className="text-slate-500/80 select-none pr-3 text-right w-8 text-[10px]">
            {idx + 1}
          </span>
          <span className="text-[#abb2bf]">
            {line}
            {comment && <span className="text-slate-500/90 italic">{comment}</span>}
          </span>
        </div>
      );
    });
  };

  return (
    <div className="bg-white border border-slate-200 rounded-xl flex flex-col shadow-xs h-full min-h-[400px]">
      {/* Editor Title bar */}
      <div className="p-4 border-b border-slate-200 flex justify-between items-center bg-slate-50 rounded-t-xl shrink-0">
        <h3 className="font-sans text-sm font-bold text-slate-800 flex items-center gap-2">
          <Terminal className="w-4 h-4 text-indigo-600" />
          <span>{t('config.activeConfiguration')}</span>
        </h3>

        <div className="flex gap-1.5">
          <button
            onClick={handleCopy}
            className="p-1.5 text-slate-500 hover:text-slate-800 rounded-md hover:bg-slate-100 transition-colors"
            title={t('config.copyTitle')}
          >
            {isCopied ? <Check className="w-4 h-4 text-emerald-600" /> : <Copy className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {/* Read-only YAML view */}
      <div className="flex-1 overflow-auto bg-[#1a1b26] p-4 font-mono text-sm leading-relaxed rounded-b-xl select-text min-h-[300px]">
        <div className="space-y-0.5">{renderHighlightedYaml(yamlContent)}</div>
      </div>

      {/* Help Bar */}
      <div className="p-3 border-t border-slate-200 text-[10px] font-semibold text-slate-500 bg-slate-50 rounded-b-xl flex justify-between">
        <span>{t('config.format')}</span>
        <span>{t('config.help')}</span>
      </div>
    </div>
  );
}
