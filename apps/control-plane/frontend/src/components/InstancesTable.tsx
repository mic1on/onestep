import { useState, useMemo, ChangeEvent } from 'react';
import { Search, SlidersHorizontal, Download, Play, Square, RotateCcw, ChevronLeft, ChevronRight, Check } from 'lucide-react';
import { Instance } from '../types';
import { useI18n } from '../i18n';

interface InstancesTableProps {
  instances: Instance[];
  onRestartInstance: (uuid: string) => void;
  onToggleInstance: (uuid: string) => void;
  onExport: () => void;
}

export default function InstancesTable({
  instances,
  onRestartInstance,
  onToggleInstance,
  onExport,
}: InstancesTableProps) {
  const { t } = useI18n();
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('All');
  const [isFilterOpen, setIsFilterOpen] = useState(false);
  const [selectedUuids, setSelectedUuids] = useState<string[]>([]);
  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 5;

  const filteredInstances = useMemo(() => {
    return instances.filter((inst) => {
      const matchesSearch =
        inst.uuid.toLowerCase().includes(searchQuery.toLowerCase()) ||
        inst.hostname.toLowerCase().includes(searchQuery.toLowerCase()) ||
        inst.nodeName.toLowerCase().includes(searchQuery.toLowerCase());

      const matchesStatus =
        statusFilter === 'All' || inst.status.toLowerCase() === statusFilter.toLowerCase();

      return matchesSearch && matchesStatus;
    });
  }, [instances, searchQuery, statusFilter]);

  // Pagination logic
  const totalItems = filteredInstances.length;
  const totalPages = Math.ceil(totalItems / itemsPerPage) || 1;
  const startIndex = (currentPage - 1) * itemsPerPage;
  const paginatedInstances = useMemo(() => {
    return filteredInstances.slice(startIndex, startIndex + itemsPerPage);
  }, [filteredInstances, startIndex]);

  const handleSelectAll = (e: ChangeEvent<HTMLInputElement>) => {
    if (e.target.checked) {
      setSelectedUuids(paginatedInstances.map((inst) => inst.uuid));
    } else {
      setSelectedUuids([]);
    }
  };

  const handleSelectRow = (uuid: string) => {
    setSelectedUuids((prev) =>
      prev.includes(uuid) ? prev.filter((id) => id !== uuid) : [...prev, uuid]
    );
  };

  const isAllSelected =
    paginatedInstances.length > 0 &&
    paginatedInstances.every((inst) => selectedUuids.includes(inst.uuid));

  const getStatusLabel = (status: string) => {
    if (status === 'Running') return t('status.running');
    if (status === 'Starting') return t('status.starting');
    if (status === 'Failed') return t('status.failed');
    if (status === 'Stopped') return t('status.stopped');
    return t('status.all');
  };

  return (
    <div className="bg-white border border-slate-200 rounded-xl overflow-hidden shadow-xs">
      {/* Table Control Header */}
      <div className="p-4 border-b border-slate-200 flex flex-col sm:flex-row justify-between gap-3 bg-slate-50">
        <div className="relative flex-1 max-w-sm">
          <Search className="w-4 h-4 text-slate-400 absolute left-3 top-3" />
          <input
            type="text"
            placeholder={t('instances.filterPlaceholder')}
            value={searchQuery}
            onChange={(e) => {
              setSearchQuery(e.target.value);
              setCurrentPage(1);
            }}
            className="w-full bg-white border border-slate-200 rounded-lg pl-9 pr-4 py-2 text-xs focus:outline-hidden focus:ring-1 focus:ring-indigo-600 focus:border-indigo-600 font-medium"
          />
        </div>

        <div className="flex gap-2 shrink-0">
          {/* Status Filter Dropdown */}
          <div className="relative">
            <button
              onClick={() => setIsFilterOpen(!isFilterOpen)}
              className="flex items-center gap-1.5 px-3 py-2 border border-slate-200 rounded-lg text-slate-600 hover:bg-slate-100 transition-colors text-xs font-semibold bg-white"
            >
              <SlidersHorizontal className="w-3.5 h-3.5" />
              <span>{t('instances.statusFilter', { status: getStatusLabel(statusFilter) })}</span>
            </button>

            {isFilterOpen && (
              <div className="absolute right-0 mt-1 w-40 bg-white border border-slate-200 rounded-lg shadow-lg py-1 z-30 font-medium text-xs">
                {['All', 'Running', 'Starting', 'Failed', 'Stopped'].map((filter) => (
                  <button
                    key={filter}
                    onClick={() => {
                      setStatusFilter(filter);
                      setIsFilterOpen(false);
                      setCurrentPage(1);
                    }}
                    className="w-full text-left px-3 py-2 hover:bg-slate-50 text-slate-700 flex items-center justify-between"
                  >
                    <span>{getStatusLabel(filter)}</span>
                    {statusFilter === filter && <Check className="w-3.5 h-3.5 text-indigo-600" />}
                  </button>
                ))}
              </div>
            )}
          </div>

          <button
            onClick={onExport}
            className="flex items-center gap-1.5 px-3 py-2 border border-slate-200 rounded-lg text-slate-600 hover:bg-slate-100 transition-colors text-xs font-semibold bg-white"
          >
            <Download className="w-3.5 h-3.5" />
            <span>{t('button.export')}</span>
          </button>
        </div>
      </div>

      {/* Bulk Action Panel */}
      {selectedUuids.length > 0 && (
        <div className="bg-indigo-50 px-6 py-2 border-b border-slate-200 flex items-center justify-between text-xs font-bold text-indigo-900">
          <span>{t('instances.selected', { count: selectedUuids.length })}</span>
          <div className="flex gap-2">
            <button
              onClick={() => {
                selectedUuids.forEach((uuid) => onRestartInstance(uuid));
                setSelectedUuids([]);
              }}
              className="px-2.5 py-1 bg-white hover:bg-slate-50 border border-slate-200 rounded-md text-indigo-600 flex items-center gap-1 transition-colors"
            >
              <RotateCcw className="w-3 h-3" /> {t('button.restartSelected')}
            </button>
            <button
              onClick={() => {
                selectedUuids.forEach((uuid) => onToggleInstance(uuid));
                setSelectedUuids([]);
              }}
              className="px-2.5 py-1 bg-white hover:bg-slate-50 border border-slate-200 rounded-md text-slate-700 flex items-center gap-1 transition-colors"
            >
              <Square className="w-3 h-3" /> {t('button.stopSelected')}
            </button>
          </div>
        </div>
      )}

      {/* Main Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-left border-collapse text-xs">
          <thead>
            <tr className="bg-slate-50 border-b border-slate-200 text-slate-500 font-bold uppercase tracking-wider">
              <th className="p-4 w-12 text-center">
                <input
                  type="checkbox"
                  checked={isAllSelected}
                  onChange={handleSelectAll}
                  className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-600 w-4 h-4"
                />
              </th>
              <th className="p-4 font-bold">{t('instances.uuid')}</th>
              <th className="p-4 font-bold">{t('instances.hostname')}</th>
              <th className="p-4 font-bold">{t('instances.nodeName')}</th>
              <th className="p-4 font-bold">{t('instances.pid')}</th>
              <th className="p-4 font-bold">{t('instances.version')}</th>
              <th className="p-4 font-bold">{t('common.status')}</th>
              <th className="p-4 font-bold text-right">{t('instances.actions')}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {paginatedInstances.length === 0 ? (
              <tr>
                <td colSpan={8} className="p-8 text-center text-slate-400 font-medium">
                  {t('instances.empty')}
                </td>
              </tr>
            ) : (
              paginatedInstances.map((inst) => {
                const isSelected = selectedUuids.includes(inst.uuid);
                const isRunning = inst.status === 'Running';
                const isStarting = inst.status === 'Starting';
                const isFailed = inst.status === 'Failed';

                let badgeClass = 'bg-slate-50 text-slate-600 border-slate-100';
                if (isRunning) badgeClass = 'bg-emerald-50 text-emerald-700 border-emerald-200';
                if (isStarting) badgeClass = 'bg-amber-50 text-amber-700 border-amber-200';
                if (isFailed) badgeClass = 'bg-rose-50 text-rose-700 border-rose-200';

                return (
                  <tr
                    key={inst.uuid}
                    className={`hover:bg-slate-50/55 transition-colors font-medium text-slate-700 ${
                      isSelected ? 'bg-slate-50/30' : ''
                    }`}
                  >
                    <td className="p-4 text-center">
                      <input
                        type="checkbox"
                        checked={isSelected}
                        onChange={() => handleSelectRow(inst.uuid)}
                        className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-600 w-4 h-4"
                      />
                    </td>
                    <td className="p-4 font-mono font-bold text-slate-900">{inst.uuid}</td>
                    <td className="p-4">{inst.hostname}</td>
                    <td className="p-4 text-slate-500">{inst.nodeName}</td>
                    <td className="p-4 font-mono text-slate-400">{inst.pid}</td>
                    <td className="p-4 text-slate-500">{inst.version}</td>
                    <td className="p-4">
                      <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md font-bold uppercase tracking-wider text-[10px] border ${badgeClass}`}>
                        <span
                          className={`w-1.5 h-1.5 rounded-full ${
                            isRunning
                              ? 'bg-emerald-500'
                              : isStarting
                              ? 'bg-amber-500'
                              : isFailed
                              ? 'bg-rose-500 animate-pulse'
                              : 'bg-slate-400'
                          }`}
                        />
                        {getStatusLabel(inst.status)}
                      </span>
                    </td>
                    <td className="p-4 text-right">
                      <div className="flex justify-end gap-1.5">
                        <button
                          onClick={() => onRestartInstance(inst.uuid)}
                          className="p-1.5 rounded-md text-slate-400 hover:text-indigo-600 hover:bg-slate-100 transition-colors"
                          title={t('instances.restartTitle')}
                        >
                          <RotateCcw className="w-3.5 h-3.5" />
                        </button>
                        <button
                          onClick={() => onToggleInstance(inst.uuid)}
                          className={`p-1.5 rounded-md text-slate-400 hover:bg-slate-100 transition-colors ${
                            isRunning ? 'hover:text-amber-600' : 'hover:text-emerald-600'
                          }`}
                          title={isRunning ? t('instances.stopTitle') : t('instances.startTitle')}
                        >
                          {isRunning ? <Square className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination Footer */}
      <div className="px-6 py-4 bg-slate-50 border-t border-slate-200 flex items-center justify-between text-xs text-slate-500 font-semibold">
        <span>
          {t('instances.pagination', {
            start: startIndex + 1,
            end: Math.min(startIndex + itemsPerPage, totalItems),
            total: totalItems,
          })}
        </span>

        <div className="flex items-center gap-1.5">
          <button
            onClick={() => setCurrentPage((p) => Math.max(p - 1, 1))}
            disabled={currentPage === 1}
            className="p-1.5 border border-slate-200 rounded-md bg-white text-slate-400 hover:text-slate-700 hover:bg-slate-50 disabled:opacity-50 disabled:pointer-events-none transition-colors"
          >
            <ChevronLeft className="w-3.5 h-3.5" />
          </button>

          {Array.from({ length: totalPages }, (_, i) => i + 1).map((page) => (
            <button
              key={page}
              onClick={() => setCurrentPage(page)}
              className={`px-2.5 py-1 rounded-md text-xs font-bold transition-all ${
                  currentPage === page
                    ? 'bg-indigo-600 text-white shadow-xs'
                    : 'border border-slate-200 bg-white text-slate-600 hover:bg-slate-50'
              }`}
            >
              {page}
            </button>
          ))}

          <button
            onClick={() => setCurrentPage((p) => Math.min(p + 1, totalPages))}
            disabled={currentPage === totalPages}
            className="p-1.5 border border-slate-200 rounded-md bg-white text-slate-400 hover:text-slate-700 hover:bg-slate-50 disabled:opacity-50 disabled:pointer-events-none transition-colors"
          >
            <ChevronRight className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}
