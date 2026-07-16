import { useState, useEffect } from 'react';
import { Terminal, Save, X, Edit, Copy, Check, FileJson } from 'lucide-react';
import { Task } from '../types';
import { useI18n } from '../i18n';

interface ConfigEditorProps {
  task: Task;
  onSaveConfig: (taskId: string, newConfigYaml: string) => void;
}

export default function ConfigEditor({ task, onSaveConfig }: ConfigEditorProps) {
  const { t } = useI18n();
  const [isEditing, setIsEditing] = useState(false);
  const [yamlContent, setYamlContent] = useState(task.configYaml);
  const [isCopied, setIsCopied] = useState(false);

  useEffect(() => {
    setYamlContent(task.configYaml);
  }, [task.configYaml]);

  const handleSave = () => {
    onSaveConfig(task.id, yamlContent);
    setIsEditing(false);
  };

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

          {!isEditing ? (
            <button
              onClick={() => setIsEditing(true)}
              className="flex items-center gap-1 px-2.5 py-1 text-xs font-bold text-indigo-600 hover:bg-indigo-50 border border-indigo-200 rounded-md transition-colors"
            >
              <Edit className="w-3.5 h-3.5" />
              <span>{t('button.modify')}</span>
            </button>
          ) : (
            <div className="flex gap-1">
              <button
                onClick={handleSave}
                className="flex items-center gap-1 px-2.5 py-1 text-xs font-bold text-white bg-indigo-600 hover:bg-indigo-800 rounded-md transition-colors shadow-xs"
              >
                <Save className="w-3.5 h-3.5" />
                <span>{t('button.save')}</span>
              </button>
              <button
                onClick={() => {
                  setYamlContent(task.configYaml);
                  setIsEditing(false);
                }}
                className="flex items-center gap-1 px-2 py-1 text-xs font-bold text-slate-500 hover:text-slate-700 hover:bg-slate-100 rounded-md transition-colors"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Editor Workarea */}
      <div className="flex-1 overflow-auto bg-[#1a1b26] p-4 font-mono text-sm leading-relaxed rounded-b-xl select-text min-h-[300px]">
        {isEditing ? (
          <textarea
            value={yamlContent}
            onChange={(e) => setYamlContent(e.target.value)}
            className="w-full h-full min-h-[320px] bg-transparent text-[#abb2bf] font-mono text-xs border-0 outline-hidden focus:ring-0 p-0 resize-none leading-relaxed"
            style={{ tabSize: 2 }}
          />
        ) : (
          <div className="space-y-0.5">{renderHighlightedYaml(yamlContent)}</div>
        )}
      </div>

      {/* Help Bar */}
      <div className="p-3 border-t border-slate-200 text-[10px] font-semibold text-slate-500 bg-slate-50 rounded-b-xl flex justify-between">
        <span>{t('config.format')}</span>
        <span>{t('config.help')}</span>
      </div>
    </div>
  );
}
