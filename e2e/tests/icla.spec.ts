import { expect, test } from './support/fixtures';

// The ICLA submission form, as somebody with no account fills it in. The harness has no mail relay, so
// a submission is kept and not mailed, and the page says so (tests/test_icla.py).

const fillIn = async (page: import('@playwright/test').Page) => {
  await page.getByLabel('Full name', { exact: true }).fill('Ada Lovelace');
  await page.getByLabel('Email').fill('ada@example.org');
  await page.getByLabel('Postal address').fill('12 St James Square\nLondon');
  await page.getByLabel('Country').fill('United Kingdom');
  await page.getByLabel('Preferred Apache ids').fill('ada');
};

test('somebody with no account signs the ICLA by typing their name, and is told where it is', async ({ page }) => {
  await page.goto('/icla?invitation=e2e-invitation');

  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Individual Contributor License Agreement');
  await fillIn(page);
  await page.getByLabel('Type your name to sign').check();
  await page.getByLabel('Your full name, to sign').fill('Ada Lovelace');
  await page.getByLabel(/I agree to the terms/).check();
  await page.screenshot({ path: 'screenshots/icla-form.png', fullPage: true });
  await page.getByRole('button', { name: 'Send the ICLA' }).click();

  // the harness has no relay: kept, and said not to be with the Secretary yet
  await expect(page.getByRole('heading', { name: 'ICLA received, not yet sent on' })).toBeVisible();
  await page.screenshot({ path: 'screenshots/icla-sent.png', fullPage: false });
});

test('somebody downloads the ICLA filled in, signs it, and uploads it', async ({ page }) => {
  await page.goto('/icla');
  await fillIn(page);
  await page.getByLabel(/Sign the ICLA as a PDF/).check();

  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.getByRole('button', { name: 'Download the ICLA' }).click()
  ]);
  expect(download.suggestedFilename()).toBe('icla.pdf');
  const filled = await download.path();

  await page.getByLabel('The signed ICLA (PDF)').setInputFiles(filled);
  await page.getByRole('button', { name: 'Send the ICLA' }).click();

  // the harness has no relay: kept, and said not to be with the Secretary yet
  await expect(page.getByRole('heading', { name: 'ICLA received, not yet sent on' })).toBeVisible();
});

test('the form says what is missing beside each field, rather than sending it', async ({ page }) => {
  await page.goto('/icla');
  await page.getByRole('button', { name: 'Send the ICLA' }).click();

  await expect(page.getByText('Full name is required')).toBeVisible();
  await expect(page.getByText('Choose how you sign')).toBeVisible();
  await expect(page.getByRole('heading', { name: /ICLA (sent|received)/ })).toHaveCount(0);
});
