import React, { useState } from 'react';
import { AlertCircle, AlertTriangle, CheckCircle2 } from 'lucide-react';
import type { AgentResult } from '../types';
import { StatusBadge } from './StatusBadge';
import { TopSourcesPreview } from './TopSourcesPreview';
import { EvidenceDrawer } from './EvidenceDrawer';

const NESTED_RECORD_KEYS = new Set([
  'line_items',
  'tax_summary',
  'attendees',
  'decisions',
  'action_items',
  'evidence_ref_ids',
]);

function scalar(value: unknown): string {
  if (typeof value === 'number') return new Intl.NumberFormat('vi-VN').format(value);
  if (typeof value === 'boolean') return value ? 'Có' : 'Không';
  if (value == null || value === '') return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function humanize(key: string): string {
  return key.replaceAll('_', ' ');
}

const ObjectTable: React.FC<{ title: string; rows: unknown[] }> = ({ title, rows }) => {
  if (!rows.length) return null;
  if (!rows.every((row) => row && typeof row === 'object' && !Array.isArray(row))) {
    return (
      <div>
        <p className="mb-1.5 text-xs font-medium capitalize text-[#949089]">{humanize(title)}</p>
        <ol className="list-inside list-decimal space-y-1 text-sm text-[#f3f2ef]">
          {rows.map((row, index) => (
            <li key={index}>{scalar(row)}</li>
          ))}
        </ol>
      </div>
    );
  }
  const objectRows = rows as Array<Record<string, unknown>>;
  const columns = Array.from(
    new Set(objectRows.flatMap((row) => Object.keys(row)))
  ).filter((key) => key !== 'evidence_ref_ids');
  return (
    <div>
      <p className="mb-1.5 text-xs font-medium capitalize text-[#949089]">
        {humanize(title)} · {rows.length}
      </p>
      <div className="overflow-x-auto rounded-lg border border-[#33312e]">
        <table className="min-w-full text-left text-xs">
          <thead className="bg-[#1b1a17] text-[#6c6862]">
            <tr>
              {columns.map((column) => (
                <th key={column} className="whitespace-nowrap px-3 py-2 font-medium">
                  {humanize(column)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {objectRows.map((row, index) => (
              <tr key={index} className="border-t border-[#33312e] text-[#f3f2ef]">
                {columns.map((column) => (
                  <td key={column} className="max-w-[320px] px-3 py-2 align-top">
                    {scalar(row[column])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

const RecordsView: React.FC<{ records: Array<Record<string, unknown>> }> = ({
  records,
}) => (
  <div className="space-y-4">
    <p className="text-xs font-medium text-[#949089]">
      Records · {records.length}
    </p>
    {records.map((record, index) => (
      <div key={index} className="space-y-4 rounded-xl border border-[#33312e] bg-[#292825] p-4">
        <dl className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
          {Object.entries(record)
            .filter(([key]) => !NESTED_RECORD_KEYS.has(key))
            .map(([key, value]) => (
              <div key={key} className="rounded-lg bg-[#22211e] px-3 py-2">
                <dt className="text-[10px] uppercase tracking-wide text-[#6c6862]">
                  {humanize(key)}
                </dt>
                <dd className="mt-0.5 break-words text-sm text-[#f3f2ef]">
                  {scalar(value)}
                </dd>
              </div>
            ))}
        </dl>
        {Array.from(NESTED_RECORD_KEYS)
          .filter((key) => key !== 'evidence_ref_ids' && Array.isArray(record[key]))
          .map((key) => (
            <ObjectTable key={key} title={key} rows={record[key] as unknown[]} />
          ))}
      </div>
    ))}
  </div>
);

const ConflictGroups: React.FC<{ groups: Array<Record<string, unknown>> }> = ({
  groups,
}) => {
  if (!groups.length) return null;
  return (
    <div>
      <p className="mb-2 text-xs font-medium text-[#949089]">
        Conflict groups · {groups.length}
      </p>
      <div className="space-y-2">
        {groups.map((group, index) => (
          <div
            key={String(group.metric ?? group.conflict_id ?? index)}
            className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3"
          >
            <div className="flex items-center gap-2 text-sm font-medium text-amber-300">
              <AlertTriangle className="h-4 w-4" />
              {String(group.metric ?? group.conflict_id ?? `Conflict ${index + 1}`)}
            </div>
            <ObjectTable
              title="values"
              rows={
                Array.isArray(group.values)
                  ? group.values
                  : Array.isArray(group.conflicting_values)
                    ? group.conflicting_values
                    : []
              }
            />
            {'delta' in group && (
              <p className="mt-2 text-xs text-amber-200">
                Chênh lệch: {scalar(group.delta)}
              </p>
            )}
            <p className="mt-1 text-xs text-amber-200/80">
              Hai nguồn được giữ nguyên; Module 2 không tự chọn số đúng.
            </p>
          </div>
        ))}
      </div>
    </div>
  );
};

export const ResultPanel: React.FC<{ result: AgentResult }> = ({ result }) => {
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);
  const output = result.output ?? {};
  const records = Array.isArray(output.records)
    ? (output.records as Array<Record<string, unknown>>)
    : [];
  const documents = Array.isArray(output.documents)
    ? (output.documents as Array<Record<string, unknown>>)
    : [];
  const validationIssues = Array.isArray(result.validation_issues)
    ? (result.validation_issues as Array<Record<string, unknown>>)
    : [];
  const conflictGroups = Array.isArray(output.conflict_groups)
    ? (output.conflict_groups as Array<Record<string, unknown>>)
    : [];
  const conflicts = Array.isArray(output.conflicts)
    ? (output.conflicts as Array<Record<string, unknown>>)
    : [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <StatusBadge status={result.status} />
          <span className="font-mono text-xs text-[#6c6862]">{result.job_id}</span>
        </div>
        <span className="text-xs text-[#6c6862]">{result.metrics.duration_ms} ms</span>
      </div>

      <div
        className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-xs ${
          result.status === 'PARTIAL'
            ? 'border-amber-500/30 bg-amber-500/10 text-amber-200'
            : result.status === 'SUCCEEDED'
              ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200'
              : 'border-[#33312e] bg-[#292825] text-[#949089]'
        }`}
      >
        {result.status === 'SUCCEEDED' ? (
          <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        ) : (
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        )}
        <span>
          {result.status === 'PARTIAL'
            ? 'Kết quả vẫn dùng được, nhưng còn critical gap hoặc outlier chưa xác minh.'
            : result.status === 'SUCCEEDED'
              ? 'Output đã qua schema/profile validation. Conflict có đủ nguồn vẫn là kết quả thành công.'
              : 'Xem error và validation issues để biết bước cần sửa.'}
        </span>
      </div>

      {result.error && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">
          <p className="font-medium">{result.error.error_code}</p>
          <p>{result.error.user_message}</p>
        </div>
      )}

      {documents.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-medium text-[#949089]">
            Parse output · {documents.length} document
          </p>
          <div className="space-y-2">
            {documents.map((document, index) => {
              const blocks = Array.isArray(document.blocks) ? document.blocks : [];
              return (
                <div key={index} className="rounded-lg border border-[#33312e] bg-[#292825] p-3">
                  <p className="font-mono text-xs text-[#d97757]">
                    {String(document.source_ref_id ?? `source ${index + 1}`)}
                  </p>
                  <p className="mt-1 text-xs text-[#949089]">{blocks.length} normalized blocks</p>
                  <ObjectTable title="blocks" rows={blocks.slice(0, 20)} />
                </div>
              );
            })}
          </div>
        </div>
      )}

      {records.length > 0 && <RecordsView records={records} />}

      {Array.isArray(output.missing_required_fields) &&
        output.missing_required_fields.length > 0 && (
          <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
            Missing required fields: {output.missing_required_fields.join(', ')}
          </div>
        )}

      {conflicts.length > 0 && <ConflictGroups groups={conflicts} />}

      {typeof output.summary === 'string' && (
        <div className="rounded-xl border border-[#d97757]/30 bg-[#d97757]/10 p-4">
          <p className="text-[10px] uppercase tracking-wide text-[#d97757]">Neutral summary</p>
          <p className="mt-1 text-sm leading-6 text-[#f3f2ef]">{output.summary}</p>
        </div>
      )}
      <ConflictGroups groups={conflictGroups} />

      {output.extracted_facts !== null &&
      typeof output.extracted_facts === 'object' ? (
        <ObjectTable
          title="extracted facts"
          rows={Object.entries(output.extracted_facts as Record<string, unknown>).map(
            ([field, value]) => ({ field, value })
          )}
        />
      ) : null}
      {Array.isArray(output.content_context) && output.content_context.length > 0 && (
        <ObjectTable title="content context" rows={output.content_context as unknown[]} />
      )}
      {Array.isArray(output.insights) && output.insights.length > 0 && (
        <ObjectTable title="insights" rows={output.insights as unknown[]} />
      )}
      {Array.isArray(output.unverified_gaps) && output.unverified_gaps.length > 0 && (
        <ObjectTable title="unverified gaps" rows={output.unverified_gaps as unknown[]} />
      )}

      {validationIssues.length > 0 && (
        <div>
          <p className="mb-2 text-xs font-medium text-[#949089]">
            Validation issues · {validationIssues.length}
          </p>
          <ObjectTable title="validation issues" rows={validationIssues} />
        </div>
      )}

      <div>
        <TopSourcesPreview
          evidence={result.evidence_refs}
          onOpenDrawer={() => setIsDrawerOpen(true)}
        />
        <EvidenceDrawer
          isOpen={isDrawerOpen}
          onClose={() => setIsDrawerOpen(false)}
          evidence={result.evidence_refs}
        />
      </div>

      <details className="rounded-lg border border-[#33312e] bg-[#1b1a17]">
        <summary className="cursor-pointer px-3 py-2 text-xs text-[#949089]">
          Advanced · raw AgentResult
        </summary>
        <pre className="max-h-96 overflow-auto border-t border-[#33312e] p-3 text-[11px] text-[#f3f2ef]">
          {JSON.stringify(result, null, 2)}
        </pre>
      </details>
    </div>
  );
};
