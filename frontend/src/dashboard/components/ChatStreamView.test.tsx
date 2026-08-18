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
});
