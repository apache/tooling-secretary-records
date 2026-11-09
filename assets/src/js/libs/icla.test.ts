import { describe, expect, it } from 'vitest';

import { apacheIds, errorsOf, projectSent, type Project } from '@/js/libs/icla';

const PROJECTS: Project[] = [
  { id: 'arbustum', name: 'Arbustum', kind: 'pmc' },
  { id: 'kununu', name: 'Kununu (Incubating)', kind: 'podling' }
];

describe('what the ICLA form sends', () => {
  it('splits the ids typed on commas and spaces, and drops the empty', () => {
    expect(apacheIds(' ada,lovelace  ,, al ')).toEqual(['ada', 'lovelace', 'al']);
    expect(apacheIds('   ')).toEqual([]);
  });

  it('sends a project the list offers by its id, whatever its case, and anything else as typed', () => {
    expect(projectSent(' arbustum ', PROJECTS)).toBe('arbustum');
    expect(projectSent('KUNUNU (incubating)', PROJECTS)).toBe('kununu');
    expect(projectSent('A project founded yesterday', PROJECTS)).toBe('A project founded yesterday');
  });

  it("reads the server's errors by field, and says the status where it says none", async () => {
    expect(await errorsOf(new Response(JSON.stringify({ errors: { email: 'wrong' } }), { status: 400 }))).toEqual({
      email: 'wrong'
    });
    expect((await errorsOf(new Response('busy', { status: 503 }))).form).toContain('503');
  });
});
