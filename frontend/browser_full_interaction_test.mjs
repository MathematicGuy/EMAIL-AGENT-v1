import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const ARTIFACT_DIR = 'C:/Users/APC/.gemini/antigravity-cli/brain/b7f9858d-dbd9-4f58-bcb5-b6da7d0577ee';

async function runInteractiveStudioTest() {
  console.log('====================================================');
  console.log('Running End-to-End Studio Interaction Test');
  console.log('====================================================');

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  const page = await context.newPage();

  const consoleLogs = [];
  const networkLogs = [];

  page.on('console', (msg) => {
    consoleLogs.push({ type: msg.type(), text: msg.text() });
    console.log(`[DevTools Console] [${msg.type()}] ${msg.text()}`);
  });

  page.on('response', (res) => {
    networkLogs.push({
      url: res.url(),
      status: res.status(),
      contentType: res.headers()['content-type'],
    });
  });

  // Step 1: Navigate to app
  console.log('1. Navigating to landing page...');
  await page.goto('http://localhost:5173/', { waitUntil: 'networkidle' });

  // Step 2: Click "Launch Studio" button
  console.log('2. Clicking "Launch Studio" button...');
  const launchButton = page.getByRole('button', { name: /Launch Studio/i });
  await launchButton.click();
  await page.waitForTimeout(1000);

  // Capture Screenshot of Studio Dashboard
  const studioScreenshot = path.join(ARTIFACT_DIR, 'browser_studio_dashboard.png');
  await page.screenshot({ path: studioScreenshot, fullPage: true });
  console.log(`Studio screenshot saved: ${studioScreenshot}`);

  // Step 3: Inspect Dashboard Studio Elements
  console.log('3. Inspecting Dashboard Studio elements...');
  const interactiveTextareas = await page.$$('textarea');
  console.log(`Textareas found in Studio: ${interactiveTextareas.length}`);

  if (interactiveTextareas.length > 0) {
    console.log('4. Typing message into Chat input...');
    const chatInput = interactiveTextareas[0];
    await chatInput.fill('Please analyze my recent email action plans and summarize the top priorities.');
    await page.waitForTimeout(500);

    const inputScreenshot = path.join(ARTIFACT_DIR, 'browser_chat_input_filled.png');
    await page.screenshot({ path: inputScreenshot, fullPage: true });
    console.log(`Chat input screenshot saved: ${inputScreenshot}`);
  }

  // Step 4: Check tabs / navigation buttons
  const buttonsInStudio = await page.$$eval('button', (btns) =>
    btns.map((b) => ({ text: b.innerText.trim(), disabled: b.disabled }))
  );
  console.log(`Buttons in Studio: ${buttonsInStudio.length}`);

  // Step 5: DevTools Performance Timing & Metrics
  const perfMetrics = await page.evaluate(() => {
    const nav = performance.getEntriesByType('navigation')[0];
    return {
      dnsTimeMs: nav ? nav.domainLookupEnd - nav.domainLookupStart : 0,
      connectTimeMs: nav ? nav.connectEnd - nav.connectStart : 0,
      responseTimeMs: nav ? nav.responseEnd - nav.responseStart : 0,
      domInteractiveMs: nav ? nav.domInteractive : 0,
      domCompleteMs: nav ? nav.domComplete : 0,
    };
  });
  console.log('Performance Metrics:', JSON.stringify(perfMetrics, null, 2));

  await browser.close();
  console.log('====================================================');
  console.log('Studio Interactive Test Completed Successfully!');
  console.log('====================================================');
}

runInteractiveStudioTest().catch((err) => {
  console.error('Interactive Test Failed:', err);
  process.exit(1);
});
