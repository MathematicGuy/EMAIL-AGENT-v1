import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import App from './App';

describe('App Landing Page & Dashboard Navigation', () => {
  it('renders landing page by default and navigates to dashboard on clicking CTA', async () => {
    window.location.hash = '';
    render(<App />);

    // Landing Page elements (wait for lazy chunk to load)
    const ctaBtns = await screen.findAllByText('Try F-Cowork Free');
    expect(ctaBtns.length).toBeGreaterThan(0);
    expect(await screen.findByText('Multi-Model Chat')).toBeTruthy();

    // Click "Try F-Cowork Free" button
    fireEvent.click(ctaBtns[0]);

    // Dashboard elements should now be visible
    const inputs = await screen.findAllByPlaceholderText('How can I help you today?');
    expect(inputs.length).toBeGreaterThan(0);
  });

  it('navigates back to landing page on clicking taskbar logo', async () => {
    window.location.hash = '#dashboard';
    render(<App />);

    // Initially on Dashboard (wait for lazy chunk)
    const inputs = await screen.findAllByPlaceholderText('How can I help you today?');
    expect(inputs.length).toBeGreaterThan(0);

    // Click logo in taskbar to go back to Landing Page
    const logoBtns = await screen.findAllByTitle('Go to Landing Page');
    fireEvent.click(logoBtns[0]);

    const ctaBtns = await screen.findAllByText('Try F-Cowork Free');
    expect(ctaBtns.length).toBeGreaterThan(0);
  });
});
