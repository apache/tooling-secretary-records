// A `test`/`expect` every spec file imports instead of importing them from '@playwright/test'
// directly, so that what fails a test is the same for every spec: an exception the page threw,
// or something the Content Security Policy refused. Neither shows on screen.
import { test as base, expect } from '@playwright/test';
import type { BrowserContext, Locator, Page } from '@playwright/test';

// Collects what fails a test from every page of `context`, open now or later, and returns the
// check that throws on any of it.
async function watch(context: BrowserContext): Promise<() => Promise<void>> {
  const thrown: Error[] = [];
  const refused: string[] = [];
  const listen = (page: Page) => page.on('pageerror', (error) => thrown.push(error));
  context.pages().forEach(listen);
  context.on('page', listen);
  await context.exposeBinding('__srCspRefused', (_source, directive: string, blocked: string) => {
    refused.push(`${directive} refused ${blocked}`);
  });
  await context.addInitScript(() => {
    document.addEventListener('securitypolicyviolation', (event) => {
      const report: unknown = Reflect.get(window, '__srCspRefused');
      if (typeof report === 'function') {
        report(event.violatedDirective, event.blockedURI);
      }
    });
  });
  return async () => {
    // A refusal or a throw reaches this process a message behind the page, so one in a test's
    // last step is still in flight when the test returns. A round trip to each page lets it land.
    for (const open of context.pages()) {
      await open.evaluate(() => new Promise((settled) => setTimeout(settled, 0))).catch(() => {});
    }
    if (thrown.length > 0) {
      throw new Error(
        `the page threw while the test ran:\n${thrown.map((error) => error.stack ?? error.message).join('\n')}`
      );
    }
    if (refused.length > 0) {
      throw new Error(`the Content Security Policy refused something while the test ran:\n${refused.join('\n')}`);
    }
  };
}

export const test = base.extend<{ watchNewContexts: void }>({
  // A Content Security Policy refusal throws nothing: the blocked script or handler just never
  // runs. The document says so with a securitypolicyviolation event, so a refusal fails the
  // test too, which makes every spec a check that app/security_headers.py allows what the page
  // actually does. The event rather than the console line, whose wording is the browser's.
  //
  // Both are watched on a context, not a page, so a second tab from `context.newPage()` is
  // covered, and on every context a test opens from `browser` as well as Playwright's own: a
  // spec that drives two users opens one per user.
  context: async ({ context }, use) => {
    const verify = await watch(context);
    await use(context);
    await verify();
  },
  // Takes `context` so that one is built, and watched, before `browser.newContext` is wrapped.
  watchNewContexts: [
    async ({ browser, context: _context }, use) => {
      const verifications: Array<() => Promise<void>> = [];
      const newContext = browser.newContext;
      browser.newContext = async (...args) => {
        const opened = await newContext.apply(browser, args);
        verifications.push(await watch(opened));
        return opened;
      };
      try {
        await use();
      } finally {
        browser.newContext = newContext;
      }
      for (const verify of verifications) {
        await verify();
      }
    },
    { auto: true }
  ]
});

export { expect };
export type { Locator, Page };
