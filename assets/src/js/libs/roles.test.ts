import { describe, expect, it } from 'vitest';
import { describeRoles, roleName } from '@/js/libs/roles';

describe('roleName', () => {
  it('names each role the server grants', () => {
    expect(roleName('secretary')).toBe('Secretary');
    expect(roleName('assistant_secretary')).toBe('Assistant Secretary');
    expect(roleName('secretarial_team')).toBe('secretarial team');
    expect(roleName('admin')).toBe('Admin');
  });

  it('shows a role it does not know by its own name rather than dropping it', () => {
    expect(roleName('auditor')).toBe('auditor');
  });
});

describe('describeRoles', () => {
  it('says so when there is no role at all', () => {
    expect(describeRoles([])).toBe('no role in this tool');
  });

  it('joins two with "and" and more with commas', () => {
    expect(describeRoles(['secretary'])).toBe('Secretary');
    expect(describeRoles(['secretary', 'admin'])).toBe('Secretary and Admin');
    expect(describeRoles(['assistant_secretary', 'secretarial_team', 'admin'])).toBe(
      'Assistant Secretary, secretarial team and Admin'
    );
  });
});
