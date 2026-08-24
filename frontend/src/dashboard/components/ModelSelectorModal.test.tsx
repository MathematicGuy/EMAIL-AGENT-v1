import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AVAILABLE_MODELS } from '../data/mockData';
import { ModelSelectorModal } from './ModelSelectorModal';

describe('ModelSelectorModal', () => {
  it('renders one compact row per model and applies one selection', () => {
    const onClose = vi.fn();
    const onSelectModel = vi.fn();

    render(
      <ModelSelectorModal
        isOpen
        onClose={onClose}
        selectedModel={AVAILABLE_MODELS[0]}
        onSelectModel={onSelectModel}
        anchor={{ left: 100, right: 300, top: 500, bottom: 530 }}
      />
    );

    expect(screen.getByText('Mô hình')).toBeTruthy();
    expect(screen.getByText('Gemini 3.5 Flash Lite')).toBeTruthy();
    expect(screen.getByText('MiMo v2.5 Pro')).toBeTruthy();
    expect(screen.getByText('Mistral Medium 3.5')).toBeTruthy();
    expect(screen.queryByText(/\((High|Medium|Low)\)/)).toBeNull();

    fireEvent.click(
      screen.getByRole('button', {
        name: /GPT OSS 120B/,
      })
    );

    expect(onSelectModel).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'openai/gpt-oss-120b' })
    );
    expect(onClose).toHaveBeenCalledOnce();
  });
});
