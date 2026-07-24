import type { MessageKey } from '../i18n';
import type { SourceConfig } from '../types';
import type { ResourceCatalogEntry, ResourceCatalogField } from '../api';

export type Translate = (key: MessageKey, values?: Record<string, string | number>) => string;

export interface SourceFieldRow {
  labelKey?: MessageKey;
  label?: string;
  configKey: string | null;
  mono?: boolean;
  expected?: boolean;
}

export interface SourceDetails {
  titleKey: MessageKey;
  typeLabel: string;
  rows: SourceFieldRow[];
}

function catalogEntry(kind: string, catalog: ResourceCatalogEntry[]): ResourceCatalogEntry | null {
  return catalog.find((entry) => entry.type === kind) ?? null;
}

function catalogField(entry: ResourceCatalogEntry, fieldName: string): ResourceCatalogField | null {
  return entry.fields.find((field) => field.name === fieldName) ?? null;
}

function fieldLabel(field: ResourceCatalogField, fieldName: string): string {
  return field.label ?? fieldName.split('_').map(capitalize).join(' ');
}

function capitalize(value: string): string {
  if (!value) return value;
  return `${value[0].toUpperCase()}${value.slice(1)}`;
}

function fieldIsMono(field: ResourceCatalogField): boolean {
  if (field.options.length > 0) return false;
  return field.type === 'string' || field.type === 'string_list' || field.type === 'ref';
}

function catalogRows(entry: ResourceCatalogEntry | null): SourceFieldRow[] {
  if (entry === null) return [];
  return entry.topology_fields.map((fieldName) => {
    const field = catalogField(entry, fieldName);
    return {
      label: field ? fieldLabel(field, fieldName) : fieldName,
      configKey: fieldName,
      mono: field ? fieldIsMono(field) : true,
      expected: true,
    };
  });
}

/**
 * Build the source detail descriptor for a task.
 *
 * Behavior:
 * - Always leads with "Source Type" (the kind) and "Connector Name".
 * - For each expected field: if present in config, show the value; if absent,
 *   show a "Not reported" placeholder (callers decide via `placeholderFor`).
 * - Returns enough info for the caller to render without importing i18n keys.
 */
export function buildSourceDetails(
  kind: string,
  catalog: ResourceCatalogEntry[],
): SourceDetails {
  const entry = catalogEntry(kind, catalog);

  const rows: SourceFieldRow[] = [
    { labelKey: 'topology.sourceKind', configKey: null },
    { labelKey: 'topology.connectorName', configKey: null },
    ...catalogRows(entry),
  ];

  return {
    titleKey: 'topology.sourceTitle',
    typeLabel: entry?.label ?? kind,
    rows,
  };
}

/**
 * Build the sink detail descriptor for a task. Mirrors buildSourceDetails but
 * uses the sink field schema and an analytics/storage-oriented type badge.
 */
export function buildSinkDetails(
  kind: string,
  catalog: ResourceCatalogEntry[],
): SourceDetails {
  const entry = catalogEntry(kind, catalog);

  const rows: SourceFieldRow[] = [
    { labelKey: 'topology.sourceKind', configKey: null },
    { labelKey: 'topology.connectorName', configKey: null },
    ...catalogRows(entry),
  ];

  return {
    titleKey: 'topology.sinkTitle',
    typeLabel: entry?.label ?? kind,
    rows,
  };
}

/**
 * Resolve a single row to a display string.
 * Returns null if the row should be skipped entirely (no value and not an
 * expected/placeholder field).
 */
export function resolveRowValue(
  row: SourceFieldRow,
  kind: string,
  config: SourceConfig | null,
  connectorName: string | null,
  t: Translate,
): { value: string; mono: boolean; placeholder: boolean } | null {
  // Derived rows (type / connector name).
  if (row.configKey === null) {
    if (row.labelKey === 'topology.sourceKind') {
      return { value: kind, mono: true, placeholder: false };
    }
    if (row.labelKey === 'topology.connectorName') {
      const name = connectorName?.trim();
      return { value: name || t('topology.notReported'), mono: true, placeholder: !name };
    }
    return null;
  }

  const raw = config ? config[row.configKey] : undefined;
  if (raw !== undefined && raw !== null && raw !== '') {
    return { value: formatValue(raw), mono: !!row.mono, placeholder: false };
  }

  if (row.expected) {
    return { value: t('topology.notReported'), mono: false, placeholder: true };
  }

  return null;
}

function formatValue(value: unknown): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return value.map(formatValue).join(', ');
  if (typeof value === 'object') {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}
