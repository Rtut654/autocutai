import { chromium } from 'playwright';
import fs from 'node:fs/promises';

const outDir = new URL('../public/screenshots/mobile/', import.meta.url);
await fs.mkdir(outDir, { recursive: true });

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 430, height: 932 } });
const page = await context.newPage();
await page.goto('http://127.0.0.1:8088', { waitUntil: 'domcontentloaded' });
await page.waitForTimeout(3000);

const screens = ['login', 'onboarding', 'billing', 'projects', 'new', 'timeline'];
for (const s of screens) {
  await page.getByText(s, { exact: true }).first().click();
  await page.waitForTimeout(400);
  await page.screenshot({ path: new URL(`${s}.png`, outDir).pathname, fullPage: true });
  console.log(`captured mobile: ${s}`);
}

await browser.close();
