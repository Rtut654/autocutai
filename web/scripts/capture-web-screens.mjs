import { chromium } from 'playwright';
import fs from 'node:fs/promises';

const pages = [
  ['home', '/'],
  ['login', '/login'],
  ['onboarding', '/onboarding'],
  ['billing', '/billing'],
  ['projects', '/projects'],
  ['new_project', '/projects/new'],
  ['timeline', '/projects/demo'],
];
const baseUrl = process.env.BASE_URL || 'http://127.0.0.1:3001';

const outDir = new URL('../public/screenshots/web/', import.meta.url);
await fs.mkdir(outDir, { recursive: true });

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 980 } });
const page = await context.newPage();

for (const [name, path] of pages) {
  await page.goto(`${baseUrl}${path}`, { waitUntil: 'networkidle' });
  await page.screenshot({ path: new URL(`${name}.png`, outDir).pathname, fullPage: true });
  console.log(`captured web: ${name}`);
}

await browser.close();
