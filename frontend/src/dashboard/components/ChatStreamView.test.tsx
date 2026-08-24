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
});
