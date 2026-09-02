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

  it('exposes attachment readiness and keeps send blocked until ready', () => {
    const baseProps = {
      inputText: 'Use the attached document',
      onChangeText: vi.fn(),
      onSend: vi.fn(),
      selectedModel: model,
      onOpenModelModal: vi.fn(),
      onOpenVoiceModal: vi.fn(),
    };
    const { rerender } = render(
      <ChatInputBox
        {...baseProps}
        attachments={[{
          id: 'upload-1',
          name: 'fixture.pdf',
          mediaType: 'application/pdf',
          sizeBytes: 2_048,
          status: 'processing',
          documentId: 'document-1',
        }]}
      />
    );

    const attachment = screen.getByTestId('chat-attachment');
    expect(attachment.getAttribute('data-attachment-status')).toBe('processing');
    expect(attachment.getAttribute('data-document-id')).toBe('document-1');
    expect((screen.getByTestId('chat-send') as HTMLButtonElement).disabled).toBe(true);

    rerender(
      <ChatInputBox
        {...baseProps}
        attachments={[{
          id: 'upload-1',
          name: 'fixture.pdf',
          mediaType: 'application/pdf',
          sizeBytes: 2_048,
          status: 'ready',
          documentId: 'document-1',
        }]}
      />
    );

    expect(screen.getByTestId('chat-attachment').getAttribute('data-attachment-status')).toBe('ready');
    expect((screen.getByTestId('chat-send') as HTMLButtonElement).disabled).toBe(false);
  });

  it('shows Mail, Email, and Outlook when the user types @', () => {
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

    expect(screen.getByRole('option', { name: /^Mail/ })).not.toBeNull();
    expect(screen.getByRole('option', { name: /^Email/ })).not.toBeNull();
    expect(screen.getByRole('option', { name: /^Outlook/ })).not.toBeNull();
    expect(screen.getByRole('listbox', { name: 'Gợi ý công cụ' }).closest('.overflow-visible'))
      .not.toBeNull();
    fireEvent.click(screen.getByRole('option', { name: /^Email/ }));
    expect(onChangeText).toHaveBeenCalledWith('@email ');
  });

  it('filters mentions and inserts the selected provider command', () => {
    const onChangeText = vi.fn();
    render(
      <ChatInputBox
        inputText="Please scan @out"
        onChangeText={onChangeText}
        onSend={vi.fn()}
        selectedModel={model}
        onOpenModelModal={vi.fn()}
        onOpenVoiceModal={vi.fn()}
      />
    );

    expect(screen.getAllByRole('option')).toHaveLength(1);
    fireEvent.click(screen.getByRole('option', { name: /^Outlook/ }));
    expect(onChangeText).toHaveBeenCalledWith('Please scan @outlook ');
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

  it('renders worker error alert and error status badge when attachment fails', () => {
    render(
      <ChatInputBox
        inputText=""
        onChangeText={vi.fn()}
        onSend={vi.fn()}
        selectedModel={model}
        onOpenModelModal={vi.fn()}
        onOpenVoiceModal={vi.fn()}
        attachmentError="Project document worker unavailable"
        attachments={[{
          id: 'upload-err',
          name: '49_2019_QH14_402073.docx',
          mediaType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
          sizeBytes: 2_048,
          status: 'error',
        }]}
      />
    );

    const alert = screen.getByRole('alert');
    expect(alert.textContent).toBe('Project document worker unavailable');
    const attachment = screen.getByTestId('chat-attachment');
    expect(attachment.textContent).toContain('49_2019_QH14_402073.docx');
    expect(attachment.textContent).toContain('Lỗi');
  });
});

