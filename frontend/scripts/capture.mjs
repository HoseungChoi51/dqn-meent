import { chromium } from '@playwright/test';
import { existsSync } from 'node:fs';

const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || (existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined) });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  await page.goto(process.argv[2] || 'http://127.0.0.1:8765/#comparison', { waitUntil: 'domcontentloaded' });
  await page.getByRole('heading', { level: 1 }).waitFor();
  await page.waitForTimeout(1500);
  await page.screenshot({ path: process.argv[3] || '/tmp/grating-workspace.png', fullPage: true });
} finally {
  await browser.close();
}
