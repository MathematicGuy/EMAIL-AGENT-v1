import type { DemoScenario } from '../types';

const METRICS = {
  duration_ms: 250,
  worker_version: 'module-2-executor/0.1.0',
  started_at: '2026-07-24T08:00:00Z',
  completed_at: '2026-07-24T08:00:00.250Z',
};

const BASE_RESULT = {
  evidence_refs: [],
  validation_issues: [],
  metrics: METRICS,
};

export const DEMO_SCENARIOS: DemoScenario[] = [
  {
    id: 'document_query_answered',
    fixtureName: 'contract.pdf',
    operation: 'query',
    description: 'Question answered from bounded passages with source locators.',
    result: {
      ...BASE_RESULT,
      job_id: 'job_query_answered',
      status: 'SUCCEEDED',
      output: {
        answerability: 'ANSWERED',
        content_context: [
          {
            context_id: 'ctx_1',
            text: 'Payment term: 30 days after invoice receipt.',
            source_ref_id: 'contract',
            locator: { page: 2 },
            relevance_score: 1,
          },
        ],
        extracted_facts: { payment_term: { value: '30 days', evidence_id: 'ev_1' } },
        insights: [],
        unverified_gaps: [],
        data_quality: {},
      },
    },
  },
  {
    id: 'invoice_extract_success',
    fixtureName: 'invoice.pdf',
    operation: 'extract',
    description: 'Structured invoice extraction with evidence.',
    result: {
      ...BASE_RESULT,
      job_id: 'job_extract',
      status: 'SUCCEEDED',
      output: { records: [], missing_required_fields: [], conflicts: [] },
    },
  },
  {
    id: 'general_analysis_success',
    fixtureName: 'report.xlsx',
    operation: 'analyze',
    description: 'General evidence-backed summary and insights.',
    result: {
      ...BASE_RESULT,
      job_id: 'job_analyze',
      status: 'SUCCEEDED',
      output: {
        summary: 'The source contains one supported trend.',
        content_context: [],
        insights: [],
        unverified_gaps: [],
        data_quality: {},
      },
    },
  },
  {
    id: 'query_insufficient_evidence',
    fixtureName: 'notes.docx',
    operation: 'query',
    description: 'Missing evidence is returned as NEEDS_INPUT.',
    result: {
      ...BASE_RESULT,
      job_id: 'job_query_missing',
      status: 'NEEDS_INPUT',
      output: {
        answerability: 'INSUFFICIENT_EVIDENCE',
        content_context: [],
        extracted_facts: {},
        insights: [],
        unverified_gaps: ['payment_term'],
        data_quality: {},
      },
    },
  },
];
