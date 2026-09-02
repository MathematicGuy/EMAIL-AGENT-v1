import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const ARTIFACT_DIR = 'C:/Users/APC/.gemini/antigravity-cli/brain/b7f9858d-dbd9-4f58-bcb5-b6da7d0577ee';

async function runBrowserTestSuite() {
  console.log('====================================================');
  console.log('Starting Browser Verification Suite (3 Browser Skills)');
  console.log('====================================================');

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 },
  });
  const page = await context.newPage();

  const consoleMessages = [];
  const networkRequests = [];
  const networkErrors = [];

  // 1. Chrome DevTools Hook: Listen to Console
  page.on('console', (msg) => {
    const entry = { type: msg.type(), text: msg.text(), location: msg.location() };
    consoleMessages.push(entry);
    console.log(`[Browser Console ${msg.type().toUpperCase()}] ${msg.text()}`);
  });

  page.on('pageerror', (err) => {
    console.error(`[Browser Uncaught Error] ${err.message}`);
    consoleMessages.push({ type: 'pageerror', text: err.message });
  });

  // 2. Chrome DevTools Hook: Listen to Network
  page.on('request', (req) => {
    networkRequests.push({
      url: req.url(),
      method: req.method(),
      resourceType: req.resourceType(),
    });
  });

  page.on('requestfailed', (req) => {
    const failure = req.failure();
    networkErrors.push({
      url: req.url(),
      method: req.method(),
      errorText: failure ? failure.errorText : 'Unknown failure',
    });
    console.warn(`[Network Request Failed] ${req.method()} ${req.url()} - ${failure?.errorText}`);
  });

  // 3. Navigation
  console.log('\n--- Step 1: Navigating to http://localhost:5173/ ---');
  const response = await page.goto('http://localhost:5173/', {
    waitUntil: 'networkidle',
    timeout: 15000,
  });

  console.log(`HTTP Status: ${response.status()}`);
  console.log(`Page Title: "${await page.title()}"`);

  // Capture screenshot #1: Initial Landing View
  const screenshot1Path = path.join(ARTIFACT_DIR, 'browser_landing_view.png');
  await page.screenshot({ path: screenshot1Path, fullPage: true });
  console.log(`Screenshot saved: ${screenshot1Path}`);

  // 4. Inspect DOM Structure & Elements
  console.log('\n--- Step 2: DOM & UI Component Inspection ---');
  const headings = await page.$$eval('h1, h2, h3', (els) =>
    els.map((e) => ({ tag: e.tagName.toLowerCase(), text: e.innerText.trim() }))
  );
  console.log('Headings found:', JSON.stringify(headings, null, 2));

  const buttons = await page.$$eval('button', (els) =>
    els.map((b) => ({ text: b.innerText.trim(), disabled: b.disabled }))
  );
  console.log('Buttons count:', buttons.length);
  console.log('Buttons sample:', buttons.slice(0, 8));

  // 5. Test Interactive UI Flow (Playwright skill)
  console.log('\n--- Step 3: Interactive UI Simulation (Playwright) ---');

  // Check for chat input or interactive inputs
  const inputs = await page.$$('textarea, input[type="text"]');
  console.log(`Interactive inputs found: ${inputs.length}`);
  if (inputs.length > 0) {
    const input = inputs[0];
    await input.fill('Hello Cowork Agent, this is an automated browser verification test!');
    console.log('Successfully typed test message into input.');

    const screenshot2Path = path.join(ARTIFACT_DIR, 'browser_input_interaction.png');
    await page.screenshot({ path: screenshot2Path, fullPage: true });
    console.log(`Screenshot saved: ${screenshot2Path}`);
  }

  // 6. Inspect Accessibility Tree & Layout Metrics
  console.log('\n--- Step 4: Accessibility & Layout Metrics (Chrome DevTools) ---');
  const layoutMetrics = await page.evaluate(() => {
    return {
      windowInnerWidth: window.innerWidth,
      windowInnerHeight: window.innerHeight,
      documentScrollWidth: document.documentElement.scrollWidth,
      documentScrollHeight: document.documentElement.scrollHeight,
      devicePixelRatio: window.devicePixelRatio,
    };
  });
  console.log('Layout Metrics:', JSON.stringify(layoutMetrics, null, 2));

  // 7. Generate Comprehensive Test Report
  const report = {
    url: 'http://localhost:5173/',
    httpStatus: response.status(),
    title: await page.title(),
    headings,
    buttonsCount: buttons.length,
    inputsCount: inputs.length,
    layoutMetrics,
    totalNetworkRequests: networkRequests.length,
    networkErrorsCount: networkErrors.length,
    consoleMessagesCount: consoleMessages.length,
    consoleMessages,
    networkErrors,
    screenshots: [
      screenshot1Path,
      path.join(ARTIFACT_DIR, 'browser_input_interaction.png'),
    ],
  };

  const reportPath = path.join(ARTIFACT_DIR, 'browser_test_report.json');
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2), 'utf-8');
  console.log(`\nBrowser test report written to: ${reportPath}`);

  await browser.close();
  console.log('\n====================================================');
  console.log('Browser Verification Suite Finished Successfully!');
  console.log('====================================================');
}

runBrowserTestSuite().catch((err) => {
  console.error('Browser Test Suite Failed:', err);
  process.exit(1);
});
