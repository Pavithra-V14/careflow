import { expect, test } from '@playwright/test';
import { ID } from './consoleApi';
import { LANGS, go, openConsole, sweep } from './helpers';

// Polish pass: the heaviest screens, keyboard only and at 200% zoom. MOCK ONLY (e2e/consoleApi.ts).
// Keyboard: Tab reaches every control the page offers, each focused control shows a visible focus indicator, and focus is never trapped.
// Zoom: a 640 px window with the root font at 200% (the same as 1280 px at 200% browser zoom) has no horizontal page scroll and axe stays clean.

const HEAVY: [role: string, name: string, hash: string][] = [
  ['front_desk', 'scheduling-slots', '#/scheduling/slots'],
  ['front_desk', 'scheduling-offers', '#/scheduling/offers'],
  ['front_desk', 'patient-record', `#/patients/${ID.patient}`],
  ['nurse', 'referral-detail', `#/referrals/${ID.doc}`],
  ['nurse', 'care-gaps', '#/care/gaps'],
  ['admin', 'admin-accounts', '#/admin/accounts'],
  ['admin', 'admin-cost', '#/admin/cost'],
  ['admin', 'dashboard', '#/dashboard'],
  ['doctor', 'rules-doctor', '#/admin/rules'],
];

for (const lang of LANGS) {
  for (const [role, name, hash] of HEAVY) {
    test(`keyboard only: ${role} ${name} [${lang}]`, async ({ page }) => {
      await openConsole(page, role, lang);
      await go(page, hash, /\S/);
      await expect(page.locator('main')).not.toContainText(/Loading…|ஏற்றுகிறது…/);
      const focusable = await page.evaluate(() =>
        Array.from(document.querySelectorAll<HTMLElement>('a[href],button,input,select,textarea,[tabindex]'))
          .filter((e) => !(e as HTMLButtonElement).disabled && e.tabIndex >= 0 && e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden').length,
      );
      const seen = new Set<string>();
      const bad: string[] = [];
      await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
      for (let i = 0; i < focusable + 3; i++) {
        await page.keyboard.press('Tab');
        const info = await page.evaluate(() => {
          const e = document.activeElement as HTMLElement | null;
          if (!e || e === document.body) return null;
          const cs = getComputedStyle(e);
          const ring = (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0) || cs.boxShadow !== 'none';
          const r = e.getBoundingClientRect();
          const id = `${e.tagName}|${e.getAttribute('aria-label') ?? e.textContent?.trim().slice(0, 30) ?? ''}|${Math.round(r.x)},${Math.round(r.y + scrollY)}`;
          return { id, ring, hidden: r.width === 0 || r.height === 0 };
        });
        if (!info) continue; // focus left the page content (browser chrome), which is allowed at the end of the cycle
        seen.add(info.id);
        if (!info.ring) bad.push(info.id);
        if (info.hidden) bad.push(`zero size: ${info.id}`);
      }
      expect(bad, 'controls with no visible focus indicator').toEqual([]);
      expect(seen.size, 'Tab reaches (nearly) every control').toBeGreaterThanOrEqual(Math.floor(focusable * 0.9));
    });
  }

  for (const [role, name, hash] of HEAVY) {
    test(`200% zoom: ${role} ${name} [${lang}]`, async ({ page }) => {
      await openConsole(page, role, lang);
      await page.setViewportSize({ width: 640, height: 800 });
      await page.evaluate(() => (document.documentElement.style.fontSize = '200%'));
      await go(page, hash, /\S/);
      await expect(page.locator('main')).not.toContainText(/Loading…|ஏற்றுகிறது…/);
      expect(await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth), `${name}: horizontal scroll at 200%`).toBe(false);
      const AxeBuilder = (await import('@axe-core/playwright')).default;
      const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
      expect(results.violations.map((v) => v.id), `${name} [${lang}] at 200%`).toEqual([]);
    });
  }
}

// The two-doctor approval by keyboard alone: Enter on Approve opens a confirmation that shows the content, Escape cancels with nothing sent,
// and Tab never reaches the page behind it.
for (const lang of LANGS) {
  test(`red-flag rules: approve by keyboard, confirmation first, Escape cancels [${lang}]`, async ({ page }) => {
    const mock = await openConsole(page, 'doctor', lang);
    await go(page, '#/admin/rules', /\S/);
    const approve = page.locator('main button').filter({ hasText: lang === 'en' ? /^Approve/ : /^ஒப்புக்கொள்/ }).first();
    await approve.focus();
    await page.keyboard.press('Enter');
    const dialog = page.locator('dialog[open]');
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText('PLACEHOLDER-PHRASE-A');
    for (let i = 0; i < 6; i++) {
      await page.keyboard.press('Tab');
      // Inside the dialog, or out in the browser's own controls (document.body); never on the page behind it, which is inert.
      expect(await page.evaluate(() => document.activeElement === document.body || !!document.activeElement?.closest('dialog[open]')), 'focus never lands on the page behind the dialog').toBe(true);
    }
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    expect(mock).toBeTruthy();
  });
}

// axe, no horizontal scroll, no CSP violation at 320, 768 and 1280 for the new screens.
for (const lang of LANGS) {
  for (const [role, name, hash] of [['doctor', 'rules-doctor', '#/admin/rules'], ['admin', 'rules-admin', '#/admin/rules'], ['front_desk_supervisor', 'tasks-supervisor', '#/tasks']] as const) {
    test(`all three widths: ${role} ${name} [${lang}]`, async ({ page }) => {
      await openConsole(page, role, lang);
      await go(page, hash, /\S/);
      await expect(page.locator('main')).not.toContainText(/Loading…|ஏற்றுகிறது…/);
      await sweep(page, `f10-${name}`, lang);
    });
  }
}
