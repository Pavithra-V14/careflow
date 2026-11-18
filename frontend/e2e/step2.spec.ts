import { expect, test } from '@playwright/test';
import { LANGS, go, openConsole, sweep } from './helpers';

// The cards and templates editor, the front-desk family link screen and the staff view of visit preparation, in a real browser against the
// fake API (e2e/consoleApi.ts, MOCK ONLY). Per screen: axe (WCAG 2.2 AA is the goal we test toward) at 320 / 768 / 1280, no horizontal
// scroll, English and Tamil. The patient side (visit preparation and family links) is swept with the portal in f11.spec.ts.
for (const lang of LANGS) {
  test(`cards and templates [${lang}]: the lists, a draft's text, the approve dialog and the new-draft form`, async ({ page }) => {
    await openConsole(page, 'doctor', lang);
    await go(page, '#/knowledge', /\S/);
    await expect(page.locator('main table').first()).toBeVisible();
    await sweep(page, 'knowledge-list', lang);
    await page.locator('main tbody tr').first().getByRole('button').first().click(); // Look
    await expect(page.locator('main blockquote')).toContainText('PLACEHOLDER body line one.');
    await sweep(page, 'knowledge-look', lang);
    await page.locator('main tbody tr').first().getByRole('button').nth(1).click(); // Approve
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'knowledge-approve-dialog', lang);
    await page.keyboard.press('Escape');
    await page.locator('main .toolbar button').first().click(); // Write a new draft
    await expect(page.locator('#kn-kind')).toBeVisible();
    await sweep(page, 'knowledge-form', lang);
  });

  test(`family link requests [${lang}]: the list and the verify dialog`, async ({ page }) => {
    await openConsole(page, 'front_desk', lang);
    await go(page, '#/family', /\S/);
    await expect(page.locator('main table')).toBeVisible();
    await sweep(page, 'family-list', lang);
    await page.locator('main tbody tr').first().getByRole('button').first().click(); // Verify
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'family-verify-dialog', lang);
  });

  test(`visit preparation, staff view [${lang}]`, async ({ page }) => {
    await openConsole(page, 'nurse', lang);
    await go(page, '#/readiness', /\S/);
    await page.locator('main tbody tr').first().getByRole('button').click(); // Open the first upcoming visit
    await page.locator('main section[aria-labelledby^="prep-"] .toolbar button').click();
    await expect(page.locator('main section[aria-labelledby^="prep-"] table')).toContainText('PLACEHOLDER item one');
    await sweep(page, 'visit-prep-staff', lang);
  });
}

test('keyboard only: approve a draft after its confirmation', async ({ page }) => {
  await openConsole(page, 'doctor', 'en');
  await go(page, '#/knowledge', /\S/);
  const approve = page.locator('main tbody tr').first().getByRole('button').nth(1);
  await approve.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('dialog').locator('.dialog-actions button').nth(1).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText('Approved as version 1.')).toBeVisible();
});
