import React from 'react';
import { ArrowLeft, BadgeCheck } from 'lucide-react';
import { LiveUploadPanel } from './components/LiveUploadPanel';

export const DocumentsDemo: React.FC<{ onNavigateHome?: () => void }> = ({
  onNavigateHome,
}) => (
  <div className="h-screen w-screen overflow-y-auto bg-[#1b1a17] text-[#f3f2ef]">
    <header className="border-b border-[#33312e] px-5 py-4 sm:px-8">
      <div className="mx-auto flex max-w-6xl items-center gap-3">
        {onNavigateHome && (
          <button
            type="button"
            onClick={onNavigateHome}
            aria-label="Quay lại trang chính"
            className="rounded-lg p-1.5 text-[#949089] transition-colors hover:bg-[#292825] hover:text-[#f3f2ef]"
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-sm font-medium">
              Documents → Artifact Workspace
            </h1>
            <BadgeCheck className="h-4 w-4 text-[#d97757]" />
          </div>
          <p className="text-xs text-[#6c6862]">
            Immutable upload → plan → approval → M2 → M3 → artifact
          </p>
        </div>
        <span className="hidden rounded-full border border-[#33312e] px-3 py-1 text-xs text-[#949089] sm:inline">
          backend canonical
        </span>
      </div>
    </header>

    <main className="mx-auto max-w-6xl px-5 py-6 sm:px-8">
      <div className="mb-6 max-w-3xl">
        <p className="text-xs font-medium uppercase tracking-[0.18em] text-[#d97757]">
          Production workflow
        </p>
        <h2 className="mt-2 text-2xl font-semibold tracking-tight">
          Từ nguồn bất biến đến artifact có kiểm soát
        </h2>
        <p className="mt-2 text-sm leading-6 text-[#949089]">
          Tải nguồn vào Resource Broker, duyệt kế hoạch của Module 1 và theo dõi
          Module 2 bàn giao dữ liệu có dẫn nguồn cho Module 3 tạo artifact.
        </p>
      </div>
      <LiveUploadPanel />
    </main>
  </div>
);

export default DocumentsDemo;
