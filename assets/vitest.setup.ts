// Unmount whatever a component test mounted, between tests.
//
// `@testing-library/svelte`'s vite plugin registers the unmount itself, and this file exists
// for the half it cannot: a component that fetches. `fetch` is global, and a test that leaves
// its own stub in place hands it to the next one in the same file, which then passes or fails
// on a stub written for a different case.
//
// `unstubAllGlobals` undoes `vi.stubGlobal` and only that. A plain `globalThis.fetch = ...`
// is not undone by it and is not undone here either - which is why assets/AGENTS.md says to
// stub rather than assign.
//
// And one setting for every component test: how long `waitFor` and the `findBy*` queries wait
// before giving up. Like vitest's own timeouts (`vitest.config.ts`), that guards against a wait
// for something that is never coming, not against slowness - and Testing Library's 1000ms is
// a budget for an idle machine, which a shared one under load runs past.
import { configure } from '@testing-library/svelte';
import { afterEach, beforeEach, vi } from 'vitest';

configure({ asyncUtilTimeout: 5000 });

beforeEach(() => {
  vi.useRealTimers();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
