import { expect, test } from '@playwright/test';
import { LANGS, go, openConsole, sweep } from './helpers';

// The front-desk screen for patients' requests about their own record, in a real browser against the fake API (e2e/consoleApi.ts, MOCK ONLY).
// Per screen: axe (WCAG 2.2 AA is the goal we test toward) at 320 / 768 / 1280, no horizontal scroll, English and Tamil.
for (const lang of LANGS) {
  test(`patient requests [${lang}]: the list and its confirmation dialog`, async ({ page }) => {
    await openConsole(page, 'front_desk', lang);
    await go(page, '#/requests', /\S/);
    await expect(page.locator('main table')).toBeVisible();
    // The third row has an empty allowed_next: no buttons, only a dash.
    await expect(page.locator('main tbody tr')).toHaveCount(3);
    await expect(page.locator('main tbody tr').nth(2).getByRole('button')).toHaveCount(0);
    await sweep(page, 'patient-requests', lang);
    await page.locator('main tbody tr').first().getByRole('button').first().click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await sweep(page, 'patient-requests-dialog', lang);
  });
}

test('keyboard only: change a request status, after its confirmation', async ({ page }) => {
  await openConsole(page, 'front_desk', 'en');
  await go(page, '#/requests', /\S/);
  const first = page.locator('main tbody tr').first().getByRole('button').first();
  await first.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('dialog').locator('.dialog-actions button').nth(1).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByText('Recorded. The patient has been told.')).toBeVisible();
});
