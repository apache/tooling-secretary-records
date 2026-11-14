import { expect, test } from './support/fixtures';

// A uid of its own per test: the server is shared by the whole run, though the roles a login
// grants are kept in that login's session alone (see e2e_login in app/client/routes.py).

test('somebody signed out is offered a sign-in', async ({ page }) => {
  await page.goto('/');

  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Secretary Records');
  await expect(page.getByRole('link', { name: 'Sign in' }).first()).toBeVisible();
  await expect(page.getByTestId('role-summary')).toHaveCount(0);
});

test('a secretary who signs in sees who they are and where the queue will be', async ({ page }) => {
  await page.goto('/e2e/login/home-secretary?roles=secretary');

  await expect(page).toHaveURL(/\/$/);
  // drawn by the island, so this also says the bundle loaded and ran under the CSP
  await expect(page.getByTestId('role-summary')).toHaveText('Signed in as home-secretary, with Secretary.');
  await expect(page.getByTestId('queue-placeholder')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Documentation' })).toBeVisible();

  await page.screenshot({ path: 'screenshots/home-secretary.png', fullPage: true });
});

test('somebody without a role is told why there is no queue', async ({ page }) => {
  await page.goto('/e2e/login/home-nobody');

  await expect(page.getByTestId('role-summary')).toHaveText(/with no role in this tool\./);
  await expect(page.getByTestId('no-access')).toBeVisible();
  await expect(page.getByTestId('queue-placeholder')).toHaveCount(0);
});
