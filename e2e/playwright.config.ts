import { defineConfig, devices } from '@playwright/test';

// The app serves over plain HTTP for e2e (see e2e/serve.py).
const BASE_URL = process.env.SR_E2E_URL ?? 'http://127.0.0.1:8099';

// By default Playwright launches the app itself (used in CI). Set
// SR_E2E_EXTERNAL_SERVER=1 to run against a server you started separately -
// scripts/e2e-container.sh does, with the app in a container of its own.
const externalServer = process.env.SR_E2E_EXTERNAL_SERVER === '1' || process.env.SR_E2E_URL !== undefined;

export default defineConfig({
  testDir: './tests',
  // One server for the whole run (see e2e/README.md), so one worker and the specs in order.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry'
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: externalServer
    ? undefined
    : {
        // Launched from the repository root; serve.py sets SR_E2E itself.
        command: 'uv run python e2e/serve.py',
        cwd: '..',
        url: BASE_URL,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000
      }
});
