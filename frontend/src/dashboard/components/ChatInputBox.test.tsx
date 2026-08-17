import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ModelOption } from '../types';
import { ChatInputBox } from './ChatInputBox';

const model: ModelOption = {
  id: 'gemini-3.5-flash-lite',
  name: 'Gemini 3.5 Flash Lite',
  version: 'latest',
  description: 'Fast',
};

afterEach(cleanup);

describe('ChatInputBox mail mention', () => {
  it('does not render the usage promotion', () => {
    render(
      <ChatInputBox
        inputText=""
        onChangeText={vi.fn()}
        onSend={vi.fn()}
        selectedModel={model}
        onOpenModelModal={vi.fn()}
        onOpenVoiceModal={vi.fn()}
      />
    );

    expect(screen.queryByText(/more usage until/i)).toBeNull();
  });

  it('shows Mail when the user types @ and inserts @mail when selected', () => {
    const onChangeText = vi.fn();
    render(
      <ChatInputBox
        inputText="@"
        onChangeText={onChangeText}
        onSend={vi.fn()}
        selectedModel={model}
        onOpenModelModal={vi.fn()}
        onOpenVoiceModal={vi.fn()}
      />
    );

    expect(screen.getByRole('option', { name: /mail/i })).not.toBeNull();
    expect(screen.getByRole('listbox', { name: 'Gợi ý công cụ' }).closest('.overflow-visible'))
      .not.toBeNull();
    fireEvent.click(screen.getByRole('option', { name: /mail/i }));
    expect(onChangeText).toHaveBeenCalledWith('@mail ');
  });

  it('hides the Mail suggestion for a non-matching mention', () => {
    render(
      <ChatInputBox
        inputText="@calendar"
        onChangeText={vi.fn()}
        onSend={vi.fn()}
        selectedModel={model}
        onOpenModelModal={vi.fn()}
        onOpenVoiceModal={vi.fn()}
      />
    );

    expect(screen.queryByRole('option', { name: /mail/i })).toBeNull();
  });
});
