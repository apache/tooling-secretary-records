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
import { afterEach, beforeEach, vi } from 'vitest';

beforeEach(() => {
  vi.useRealTimers();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});
