// What the page calls each role the server can grant (app/models/role.py). A role the
// server sends that is not listed here is shown by its own name rather than dropped: a
// summary that leaves a role out tells the reader they hold less than they do.
const ROLE_NAMES: Record<string, string> = {
  secretary: 'Secretary',
  assistant_secretary: 'Assistant Secretary',
  secretarial_team: 'secretarial team',
  admin: 'Admin'
};

export function roleName(role: string): string {
  return ROLE_NAMES[role] ?? role;
}

// "Secretary", "Secretary and Admin", "Assistant Secretary, secretarial team and Admin".
export function describeRoles(roles: readonly string[]): string {
  const names = roles.map(roleName);
  if (names.length === 0) {
    return 'no role in this tool';
  }
  if (names.length === 1) {
    return names[0] ?? '';
  }
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}
