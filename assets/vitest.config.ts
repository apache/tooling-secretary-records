import { configDefaults, defineConfig } from 'vitest/config';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { svelteTesting } from '@testing-library/svelte/vite';
import { fileURLToPath } from 'node:url';

// Two suites, because they answer different questions and want different rooms.
//
// `logic` is the frontend model: pure modules, no DOM, milliseconds. `components` mounts a
// real component in happy-dom and drives it. Both are vitest and both run from `yarn test`.
//
// Playwright owns the flow a user walks through; this owns what a component does when an
// answer is late, which a suite needing an application container and tens of seconds a case
// cannot afford to ask. assets/AGENTS.md says which file goes where.
export default defineConfig({
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  test: {
    projects: [
      {
        extends: true,
        test: {
          name: 'logic',
          environment: 'node',
          include: ['src/**/*.test.ts'],
          // spread, because setting `exclude` replaces vitest's defaults rather than adding
          // to them - and losing them collects anything under a vendored node_modules or a
          // build output that happens to sit beneath src/
          exclude: [...configDefaults.exclude, 'src/**/*.component.test.ts']
        }
      },
      {
        extends: true,
        // Here rather than at the root: `svelteTesting` splices a browser resolve condition
        // and a setup file into whatever it is attached to, and at the root it does that to
        // the node project as well, which slows the logic suite for nothing.
        plugins: [svelte(), svelteTesting()],
        test: {
          name: 'components',
          environment: 'happy-dom',
          setupFiles: ['./vitest.setup.ts'],
          include: ['src/**/*.component.test.ts']
        }
      }
    ],
    coverage: {
      provider: 'v8',
      // The frontend-coverage meter reads this (docs/quality-ratchet.md): the logic under
      // src/js/libs, not the components, which have a suite of their own to be tested in
      // without being on the floor.
      include: ['src/js/libs/**/*.ts'],
      exclude: ['src/js/libs/**/*.test.ts'],
      reporter: ['text-summary', 'json-summary'],
      reportsDirectory: 'coverage'
    }
  }
});
