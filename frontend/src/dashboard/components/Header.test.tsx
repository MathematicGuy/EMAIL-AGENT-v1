import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Header } from './Header';

describe('Header', () => {
  it('omits the retired work intake control', () => {
    render(<Header apiStatus="online" />);

    expect(screen.queryByTitle('Work intake')).toBeNull();
  });
});
