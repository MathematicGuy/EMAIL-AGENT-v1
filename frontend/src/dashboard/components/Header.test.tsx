import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { Header } from './Header';

afterEach(cleanup);

describe('Header', () => {
  it('omits the retired work intake control', () => {
    render(<Header apiStatus="online" />);

    expect(screen.queryByTitle('Work intake')).toBeNull();
  });

  it('does not render an assistant menu control', () => {
    render(<Header apiStatus="online" />);

    expect(screen.queryByTitle('Assistant menu')).toBeNull();
  });
});
