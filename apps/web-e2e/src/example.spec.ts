import { test, expect } from '@playwright/test';

test('renders app shell', async ({ page }) => {
  await page.goto('/');

  await expect(page.locator('tui-root h1')).toHaveText('Trader');
});
