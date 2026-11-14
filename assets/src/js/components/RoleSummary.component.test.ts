import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import RoleSummary from '@/js/components/RoleSummary.svelte';

describe('RoleSummary', () => {
  it('names the reader and every role they hold', () => {
    render(RoleSummary, { uid: 'ada', roles: ['secretary', 'admin'] });

    expect(screen.getByTestId('role-summary').textContent).toBe('Signed in as ada, with Secretary and Admin.');
  });

  it('says so when the reader holds no role here', () => {
    render(RoleSummary, { uid: 'bob', roles: [] });

    expect(screen.getByTestId('role-summary').textContent).toBe('Signed in as bob, with no role in this tool.');
  });
});
