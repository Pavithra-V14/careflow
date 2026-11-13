import { expect, test } from '@playwright/test';
import { ID } from './consoleApi';
import { LANGS, go, openConsole } from './helpers';

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
