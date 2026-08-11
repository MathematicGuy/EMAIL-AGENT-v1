import type { Operation } from './types';

export type PresetId = 'invoice' | 'meeting' | 'query' | 'analysis';

export interface PresetStep {
  label: string;
  operation: Operation;
  constraints: Record<string, unknown>;
  outputSchemaRef: string;
}

export interface DocumentPreset {
  id: PresetId;
  title: string;
  description: string;
  expectedOutcome: string;
  accept: string;
  fileCount: number;
  fileHint: string;
  objective: string;
  steps: PresetStep[];
}

export const DOCUMENT_PRESETS: DocumentPreset[] = [
  {
    id: 'invoice',
    title: 'Invoice Extract',
    description: 'Đọc hóa đơn VAT Việt Nam thành record có cấu trúc và evidence.',
    expectedOutcome: '6 line items · 3 mức thuế · tổng thanh toán 182.355.000 VND',
    accept: '.pdf',
    fileCount: 1,
    fileHint: 'HoaDon_ABC_2026_0847.pdf',
    objective:
      'Trích xuất dữ liệu hóa đơn VAT có dẫn nguồn và tạo báo cáo markdown tóm tắt kết quả.',
    steps: [
      {
        label: 'Extract',
        operation: 'extract',
        constraints: {
          extraction_profile: { id: 'invoice_vn', version: '1.0' },
        },
        outputSchemaRef: 'documents.extract.invoice_vn-output-v1',
      },
    ],
  },
  {
    id: 'meeting',
    title: 'Meeting Extract',
    description: 'Giữ đúng quan hệ hàng cho người tham dự, quyết định và action item.',
    expectedOutcome: '8 attendees · 6 decisions · 6 action items',
    accept: '.docx',
    fileCount: 1,
    fileHint: 'BienBanHop_KickOff_Module2.docx',
    objective:
      'Trích xuất người tham dự, quyết định và action item, sau đó tạo biên bản họp markdown có dẫn nguồn.',
    steps: [
      {
        label: 'Extract',
        operation: 'extract',
        constraints: {
          extraction_profile: { id: 'meeting_minutes_vi', version: '1.0' },
        },
        outputSchemaRef: 'documents.extract.meeting_minutes_vi-output-v1',
      },
    ],
  },
  {
    id: 'query',
    title: 'Document Query',
    description: 'Trả lời câu hỏi từ tài liệu với passage, fact và evidence có locator.',
    expectedOutcome: 'Câu trả lời grounded hoặc thông báo thiếu evidence',
    accept: '.pdf,.docx,.md',
    fileCount: 1,
    fileHint: 'HopDong_DichVu.pdf',
    objective: 'Tìm và trả lời câu hỏi chi tiết trong tài liệu, luôn kèm nguồn dẫn.',
    steps: [
      {
        label: 'Query',
        operation: 'query',
        constraints: { answer_requirements: { required_facts: [], answer_type: 'explanation', max_passages: 5 } },
        outputSchemaRef: 'documents.query-output-v1',
      },
    ],
  },
  {
    id: 'analysis',
    title: 'General Document/Data Analysis',
    description: 'Tạo summary và insight tổng quát từ source, không áp KPI hay cleaning policy.',
    expectedOutcome: 'Summary/insights có evidence và data quality',
    accept: '.pdf,.docx,.md',
    fileCount: 1,
    fileHint: 'BaoCao_Thang.docx',
    objective:
      'Phân tích tài liệu/dữ liệu để tìm summary, xu hướng, bất thường hoặc so sánh có evidence.',
    steps: [
      {
        label: 'Analyze',
        operation: 'analyze',
        constraints: {},
        outputSchemaRef: 'documents.analyze-output-v2',
      },
    ],
  },
];

export function constraintsForEditor(
  preset: DocumentPreset
): Record<string, Record<string, unknown>> {
  return Object.fromEntries(
    preset.steps.map((step) => [step.operation, step.constraints])
  );
}
