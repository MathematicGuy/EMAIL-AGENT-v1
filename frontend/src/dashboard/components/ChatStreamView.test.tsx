import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AVAILABLE_MODELS } from '../data/mockData';
import type { ChatMessage } from '../types';
import { ChatStreamView } from './ChatStreamView';

afterEach(cleanup);

function renderStream(messages: ChatMessage[]) {
  return render(
    <ChatStreamView
      messages={messages}
      inputText=""
      onChangeText={vi.fn()}
      onSend={vi.fn()}
      isGenerating
      onStopGeneration={vi.fn()}
      selectedModel={AVAILABLE_MODELS[0]}
      onOpenModelModal={vi.fn()}
      onOpenVoiceModal={vi.fn()}
    />
  );
}

describe('ChatStreamView assistant content marker', () => {
  it('excludes static assistant chrome and receives streamed content only', () => {
    const emptyAssistant: ChatMessage = {
      id: 'assistant-1',
      role: 'assistant',
      content: '',
      timestamp: '10:15',
      isStreaming: true,
    };
    const { rerender } = renderStream([emptyAssistant]);

    expect(screen.getByText('F-Cowork AI')).not.toBeNull();
    expect(screen.getByTestId('assistant-message-content').textContent).toBe('');

    rerender(
      <ChatStreamView
        messages={[{ ...emptyAssistant, content: 'First streamed token' }]}
        inputText=""
        onChangeText={vi.fn()}
        onSend={vi.fn()}
        isGenerating
        onStopGeneration={vi.fn()}
        selectedModel={AVAILABLE_MODELS[0]}
        onOpenModelModal={vi.fn()}
        onOpenVoiceModal={vi.fn()}
      />
    );

    expect(screen.getByTestId('assistant-message-content').textContent)
      .toContain('First streamed token');
  });

  it('mounts the inline reasoning card above the assistant answer', () => {
    renderStream([
      {
        id: 'assistant-2',
        role: 'assistant',
        content: 'Câu trả lời cuối cùng.',
        timestamp: '10:16',
        generationStatus: 'completed',
        executionTrace: {
          provider: 'mimo',
          model: 'mimo-v2.5-pro',
          mode: 'reasoning',
          reasoning: 'Suy luận nội bộ của mô hình.',
          reasoningTruncated: false,
          retrievedFilenames: [],
        },
      },
    ]);

    const card = screen.getByLabelText('Suy luận của mô hình');
    expect(card).not.toBeNull();
    expect(card.textContent).toContain('mimo-v2.5-pro');
    expect(screen.getByTestId('assistant-message-content').textContent)
      .toContain('Câu trả lời cuối cùng.');
  });

  it('deduplicates multiple chunk citations from the same file into a single file pill', () => {
    renderStream([
      {
        id: 'assistant-citations',
        role: 'assistant',
        content: 'Nội dung trả lời từ tài liệu.',
        timestamp: '10:17',
        generationStatus: 'completed',
        citations: [
          {
            citationId: 'c1',
            projectId: 'p1',
            documentId: 'doc1',
            documentTitle: 'cap_lai_cccd.pdf',
            section: 'Quy trình online',
            pageStart: 1,
            pageEnd: 1,
          },
          {
            citationId: 'c2',
            projectId: 'p1',
            documentId: 'doc1',
            documentTitle: 'cap_lai_cccd.pdf',
            section: 'Các trường hợp cấp đổi',
            pageStart: 1,
            pageEnd: 1,
          },
          {
            citationId: 'c3',
            projectId: 'p1',
            documentId: 'doc1',
            documentTitle: 'cap_lai_cccd.pdf',
            section: 'Địa điểm làm thẻ',
            pageStart: 2,
            pageEnd: 2,
          },
        ],
      },
    ]);

    // Should only render 1 pill for cap_lai_cccd.pdf instead of 3
    const pills = screen.getAllByText('cap_lai_cccd.pdf');
    expect(pills).toHaveLength(1);
  });

  it('renders an artifact card and dispatches navigate-to-artifacts when clicked', () => {
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent');
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem');

    renderStream([
      {
        id: 'assistant-artifact',
        role: 'assistant',
        content: 'Đã tạo báo cáo thành công.',
        timestamp: '10:18',
        generationStatus: 'completed',
        artifactRefs: [
          {
            ref_id: 'bao-cao-tong-hop-cccd.md',
            checksum: '',
            provenance: {
              upload_filename: 'bao-cao-tong-hop-cccd.md',
              title: 'Báo cáo tổng hợp quy trình CCCD',
            },
          },
        ],
      },
    ]);

    expect(screen.getByText('Artifact Báo cáo')).toBeTruthy();
    expect(screen.getByText('Báo cáo tổng hợp quy trình CCCD')).toBeTruthy();
    expect(screen.getByText('bao-cao-tong-hop-cccd.md')).toBeTruthy();

    const viewButton = screen.getByRole('button', { name: /Xem trong Artifacts/ });
    viewButton.click();

    expect(setItemSpy).toHaveBeenCalledWith('selected_artifact_filename', 'bao-cao-tong-hop-cccd.md');
    expect(dispatchSpy).toHaveBeenCalledWith(expect.objectContaining({ type: 'navigate-to-artifacts' }));
  });
});

