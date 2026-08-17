import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { RagEvidencePanel } from './RagEvidencePanel';
import type { ChatRagEvidence } from '../types';

afterEach(cleanup);

const evidence: ChatRagEvidence[] = [{
  source: 'company_knowledge',
  retrievalStatus: 'success',
  chunkId: 'chunk-1',
  documentId: 'document-1',
  documentTitle: 'Residence guide',
  section: 'Article 27',
  sourceUrl: null,
  relevanceScore: 0.842,
  rerankScore: 0.917,
  preview: 'A relevant preview from the retrieved document.',
  content: 'The complete retrieved chunk used to answer this question.',
}];

describe('RagEvidencePanel', () => {
  it('starts collapsed and reveals ranked scores and previews when opened', () => {
    render(<RagEvidencePanel evidence={evidence} retrievalStatus="success" />);

    const disclosure = screen.getByText('RAG evidence · 1 chunk · success').closest('details');
    expect(disclosure?.open).toBe(false);
    expect(screen.queryByText('A relevant preview from the retrieved document.')).toBeNull();

    fireEvent.click(screen.getByText('RAG evidence · 1 chunk · success'));
    expect(screen.getByText(/Residence guide/)).toBeTruthy();
    expect(screen.getByText('Article 27')).toBeTruthy();
    expect(screen.getByText(/Relevance 0.842/)).toBeTruthy();
    expect(screen.getByText(/Rerank 0.917/)).toBeTruthy();
    expect(screen.getByText('A relevant preview from the retrieved document.')).toBeTruthy();
  });

  it('opens the full chunk dialog and closes it with Escape', () => {
    render(<RagEvidencePanel evidence={evidence} retrievalStatus="success" />);
    fireEvent.click(screen.getByText('RAG evidence · 1 chunk · success'));
    fireEvent.click(screen.getByRole('button', { name: 'View full chunk' }));

    const dialog = screen.getByRole('dialog', { name: 'Retrieved chunk: Residence guide' });
    expect(dialog.textContent).toContain('The complete retrieved chunk used to answer this question.');

    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('falls back to preview when list history omitted chunk content', async () => {
    const slim: ChatRagEvidence[] = [{ ...evidence[0], content: '' }];
    render(<RagEvidencePanel evidence={slim} retrievalStatus="success" />);
    fireEvent.click(screen.getByText('RAG evidence · 1 chunk · success'));
    fireEvent.click(screen.getByRole('button', { name: 'View full chunk' }));
    expect(screen.getByRole('dialog').textContent).toContain(
      'A relevant preview from the retrieved document.',
    );
  });

  it('shows the retrieval state when no chunks were found', () => {
    render(<RagEvidencePanel evidence={[]} retrievalStatus="no_results" />);
    fireEvent.click(screen.getByText('RAG evidence · no results'));
    expect(screen.getByText('No matching chunks were retrieved.')).toBeTruthy();
  });
});
