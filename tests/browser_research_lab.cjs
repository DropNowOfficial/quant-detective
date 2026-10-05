/* Behavioral contract for the offline teaching UI. No market data is embedded. */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const runtime = process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES;
const { chromium } = require(runtime ? path.join(runtime, 'playwright') : 'playwright');

const dates = ['2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07'];
const nav = [[10000, 0, 0, 0, false], [9900, -0.01, 0.2, 1, false], [10100, 0, 0.2, 1, false], [11000, 0, 0, 0, false]];
const metrics = { cagr: 0.1, total_return: 0.1, max_drawdown: -0.01, volatility: 0.15, sharpe_rf0: 0.7, mean_exposure: 0.1, completed_trades: 1, win_rate: 1, profit_factor: null, terminal_equity: 11000, year_returns: [{ year: 2025, return: 0.1, partial: true }] };
const trade = { symbol: 'TEST', signal_date: dates[0], entry_date: dates[1], exit_date: dates[3], entry_price: 100, exit_price: 110, quantity: 2, pnl: 18, net_return: 0.09, holding_sessions: 3, stale: false, delayed_exit: false };
const secondTrade = { ...trade, symbol: 'OTHER', pnl: -12, net_return: -0.06 };
const scenarios = [];
for (const variant of ['primary', 'completed_ma5', 'volume08', 'trend']) for (const hold of [1, 3, 5, 10]) for (const cost_bps of [0, 10, 25, 50]) {
  scenarios.push({ id: `${variant}-${hold}-${cost_bps}`, variant, label: variant, hold, cost_bps, status: 'DIAGNOSTIC', metrics: { ...metrics, cagr: cost_bps === 50 ? 0.07 : 0.1 }, nav, stale_days: 0, fees: 2, entry_count: 1, open_positions: 0 });
}
const dataset = { id: 'fixture', label: '行为测试样本', start: dates[0], end: dates[3], dates, source_rows: 16, invalid_rows: 0, calendar_status: 'VALID', scenarios, baselines: { QQQ: { status: 'DIAGNOSTIC', metrics, nav }, SOXX: { status: 'DIAGNOSTIC', metrics, nav } }, primary_trades: [trade, secondTrade], lessons: [{ label: 'TEST 逐日复盘', trade, signal_index: 0, entry_index: 1, exit_index: 3, points: dates.map((date, i) => ({ date, close: 100 + i * 5, ma5: 99 + i, atr5: 3, d5: (1 + i * 4) / 3, gap: 0.01, eligible: i === 0 })) }], diagnostics: { primary_vs_trend: { annualized_mean_daily_difference: 0.02, ci95: [-0.01, 0.05] }, primary_vs_qqq: { annualized_mean_daily_difference: -0.02, ci95: [-0.04, 0.01] }, concentration: [{ symbol: 'TEST', pnl: 18, trades: 1 }] } };
const blocked = { ...dataset, id: '=ATTACK', label: '缺价样本', scenarios: scenarios.map(s => ({ ...s, status: 'BLOCKED_DATA', metrics: { ...s.metrics, cagr: null }, nav: [[10000, 0, 0, 0, false], [9900, -0.01, null, 1, true], [10100, 0, 0.2, 1, false], [11000, 0, 0, 0, false]], stale_days: 1 })), lessons: [], primary_trades: [secondTrade] };
const fixture = { version: 'test', run_id: 'fixture-run', generated_at: '2026-10-04T00:00:00Z', datasets: [dataset, blocked], sources: [{ id: 'unsafe', title: '<img src=x onerror="window.hacked=true">', authors: 'Test author', year: 2020, url: 'javascript:alert(1)', kind: 'test', claim_supported: '测试', transfer_limit: '测试' }], evidence_gates: [{ name: '完整持仓价格', status: 'BLOCKED', detail: '一条持仓日缺价' }], limits: ['测试样本，不是历史市场资料。'], manifest: [{ symbol: 'TEST', source: 'synthetic-test', known_at: '2026-10-04T00:00:00Z', sha256: 'abc123' }], data_reconciliation: { status: 'BLOCKED', detail: '测试' }, meta: {} };

let browser;
async function main() {
  browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE || undefined, args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'] });
  const context = await browser.newContext({ acceptDownloads: true });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  const source = path.resolve(__dirname, '../research_lab/template.html');
  // The blank fallback makes the RED state a missing-interface assertion.
  const template = fs.existsSync(source) ? fs.readFileSync(source, 'utf8') : '<!doctype html><title>Not implemented</title>';
  const html = template.replace('__LAB_DATA__', JSON.stringify(fixture).replace(/</g, '\\u003c'));
  await page.setContent(html);
  assert.equal(await page.locator('#dataset-select').count(), 1, 'The dataset control must exist before results can be explored.');
  assert.match(await page.locator('[data-metric="cagr"]').innerText(), /10\.0%/, 'Default result must come from primary / 3 / 10.');
  await page.locator('#cost-select').selectOption('50');
  assert.match(await page.locator('[data-metric="cagr"]').innerText(), /7\.0%/, 'Changing cost must select its actual precomputed scenario.');
  await page.locator('#dataset-select').selectOption('=ATTACK');
  assert.equal((await page.locator('[data-metric="cagr"]').innerText()).trim(), '—', 'A null CAGR must stay missing rather than becoming zero.');
  assert.equal(await page.locator('#scenario-status').getAttribute('data-state'), 'blocked', 'Blocked data must be a prominent result state.');
  const csvEvent = page.waitForEvent('download');
  await page.locator('#export-csv').click();
  const csvDownload = await csvEvent;
  const csvPath = path.join(os.tmpdir(), 'research-lab-test-nav.csv');
  await csvDownload.saveAs(csvPath);
  const csv = fs.readFileSync(csvPath, 'utf8');
  assert.match(csv, /'=ATTACK/, 'Formula-leading source strings must be neutralized in CSV.');
  assert.match(csv, /,-0\.01,/, 'Numeric negative drawdowns must remain numeric CSV cells.');
  assert.match(csv, /9900,-0\.01,,1,true/, 'Missing exposure must export as an empty cell.');
  fs.unlinkSync(csvPath);
  await page.locator('#dataset-select').selectOption('fixture');
  await page.locator('[data-tab="review"]').click();
  assert.equal(await page.locator('#lesson-date').innerText(), dates[0], 'A lesson initially reveals only the signal day.');
  assert.equal(await page.locator('#lesson-chart [data-point]').count(), 1, 'Future lesson prices must not be plotted before reveal.');
  const initialChart = await page.locator('#lesson-chart').innerHTML();
  const altered = structuredClone(fixture);
  altered.datasets[0].lessons[0].points.slice(1).forEach(p => { p.close = 999999999; p.ma5 = -999999999; p.atr5 = 999999999; });
  altered.datasets[0].lessons[0].trade.entry_price = 999999999;
  altered.datasets[0].lessons[0].trade.exit_price = -999999999;
  const futurePage = await context.newPage();
  await futurePage.setContent(template.replace('__LAB_DATA__', JSON.stringify(altered).replace(/</g, '\\u003c')));
  await futurePage.locator('[data-tab="review"]').click();
  assert.equal(await futurePage.locator('#lesson-chart').innerHTML(), initialChart, 'Unrevealed future prices must not alter the chart or its axes.');
  await futurePage.close();
  await page.locator('#lesson-next').click();
  assert.equal(await page.locator('#lesson-date').innerText(), dates[1], 'A reveal action advances exactly one session.');
  assert.equal(await page.locator('#lesson-chart [data-point]').count(), 2);
  await page.locator('#lesson-reset').click();
  assert.equal(await page.locator('#lesson-date').innerText(), dates[0]);
  await page.locator('#trade-search').fill('OTHER');
  assert.equal(await page.locator('#trade-list tr[data-trade]').count(), 1, 'Search must filter the real ledger.');
  await page.locator('#trade-list tr[data-trade]').click();
  assert.equal(await page.locator('#lesson-next').isDisabled(), true, 'A ledger-only trade must not gain invented daily bars.');
  await page.locator('[data-tab="principles"]').click();
  const before = await page.locator('#synthetic-distance').innerText();
  await page.locator('#sim-close').fill('110');
  await page.locator('#sim-close').dispatchEvent('input');
  assert.notEqual(await page.locator('#synthetic-distance').innerText(), before, 'Synthetic indicator inputs must recalculate their formula output.');
  await page.locator('[data-tab="sources"]').click();
  assert.equal(await page.evaluate(() => !!window.hacked), false, 'Evidence titles must never execute as HTML.');
  assert.equal(await page.locator('a[href^="javascript:"]').count(), 0, 'Evidence links must reject active-content protocols.');
  const manifestEvent = page.waitForEvent('download');
  await page.locator('#export-manifest').click();
  const manifestDownload = await manifestEvent;
  const manifestPath = path.join(os.tmpdir(), 'research-lab-test-manifest.json');
  await manifestDownload.saveAs(manifestPath);
  assert.deepEqual(JSON.parse(fs.readFileSync(manifestPath, 'utf8')).manifest, fixture.manifest, 'Manifest download must preserve provenance fields.');
  fs.unlinkSync(manifestPath);
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    for (const tab of ['bench', 'review', 'principles', 'sources', 'gates']) {
      await page.locator(`[data-tab="${tab}"]`).click();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true, `${tab} must not cause page overflow at ${width}px.`);
    }
  }
  assert.deepEqual(errors, [], 'The standalone UI must have no JavaScript runtime errors.');
  await browser.close();
  console.log('PASS: research lab scenario selection, null semantics, exports, reveal, filtering, synthetic formulas, XSS protection, 390/320px layout.');
}
main().catch(async err => { console.error(err); if (browser) await browser.close(); process.exitCode = 1; });
