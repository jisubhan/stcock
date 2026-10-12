import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { chromium, expect as playwrightExpect } from '@playwright/test';

// Serve the web app normally (npm run dev or npm start) before running this file.
// Each viewport gets a real, disposable API and database. Browser API requests
// are forwarded there, so notification checks never change the user's data.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const baseURL = process.env.E2E_BASE_URL || 'http://127.0.0.1:3000';
const artifacts = process.env.E2E_ARTIFACT_DIR || '/tmp/flowtrade-radar-e2e';
const executablePath = process.env.CHROMIUM_PATH || '/usr/bin/chromium';
const python = process.env.E2E_PYTHON || join(root, '.venv/bin/python');
const timeout = 30_000;
let passed = 0;

const expect = playwrightExpect.configure({ timeout });
await mkdir(artifacts, { recursive: true });

async function availablePort() {
  const server = createServer();
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const port = server.address().port;
  await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
  return port;
}

async function startIsolatedApi() {
  const dataDir = await mkdtemp(join(tmpdir(), 'flowtrade-smoke-'));
  const port = await availablePort();
  const origin = `http://127.0.0.1:${port}`;
  const process = spawn(python, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port)], {
    cwd: join(root, 'apps/api'),
    env: { ...globalThis.process.env, API_DATA_DIR: dataDir, TOSS_DATA_MODE: 'demo' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let output = '';
  let spawnError;
  process.on('error', error => { spawnError = error; });
  process.stdout.on('data', chunk => { output = (output + chunk).slice(-6000); });
  process.stderr.on('data', chunk => { output = (output + chunk).slice(-6000); });
  const stop = async () => {
    if (process.exitCode === null && !spawnError) {
      process.kill('SIGTERM');
      await Promise.race([new Promise(resolve => process.once('exit', resolve)), delay(5000)]);
      if (process.exitCode === null) process.kill('SIGKILL');
    }
    await rm(dataDir, { recursive: true, force: true });
  };
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (spawnError || process.exitCode !== null) {
      await stop();
      throw new Error(`Isolated API could not start: ${spawnError?.message || output}`);
    }
    try {
      const response = await fetch(`${origin}/api/health`);
      if (response.ok) return { origin, stop };
    } catch { /* The socket is not listening until Uvicorn is ready. */ }
    await delay(100);
  }
  await stop();
  throw new Error(`Isolated API startup timed out: ${output}`);
}

async function paint(page) {
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

async function noOverflow(page, width) {
  const dimensions = await page.evaluate(() => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }));
  assert.ok(dimensions.document <= width + 1 && dimensions.body <= width + 1,
    `Horizontal page overflow at ${width}px: ${JSON.stringify(dimensions)}`);
}

const browser = await chromium.launch({ executablePath, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
try {
  for (const viewport of [{ name: 'desktop', width: 1440, height: 1000 }, { name: 'mobile', width: 390, height: 844 }]) {
    const isolatedApi = await startIsolatedApi();
    const context = await browser.newContext({ viewport, locale: 'ko-KR', timezoneId: 'Asia/Seoul', deviceScaleFactor: 1, reducedMotion: 'reduce' });
    const page = await context.newPage();
    page.setDefaultTimeout(timeout);
    const errors = [];
    const failedApis = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => {
      const path = new URL(response.url()).pathname;
      if (path.startsWith('/api/') && response.status() >= 400 && path !== '/api/stocks/999999') {
        failedApis.push(`${response.status()} ${path}`);
      }
    });
    await context.addInitScript(() => {
      if (!sessionStorage.getItem('flowtrade-smoke-initialized')) {
        localStorage.setItem('flowtrade-watchlist', '[]');
        sessionStorage.setItem('flowtrade-smoke-initialized', 'true');
      }
    });
    await context.route('**/api/**', async route => {
      const request = route.request();
      const url = new URL(request.url());
      const response = await context.request.fetch(`${isolatedApi.origin}${url.pathname}${url.search}`, {
        method: request.method(),
        headers: { 'Content-Type': request.headers()['content-type'] || 'application/json' },
        data: request.postData() || undefined,
      });
      await route.fulfill({ response });
    });

    const check = async (name, work) => {
      await work();
      assert.deepEqual(errors, [], `Browser runtime errors: ${errors.join('; ')}`);
      assert.deepEqual(failedApis, [], `Failed application API requests: ${failedApis.join('; ')}`);
      passed += 1;
      console.log(`PASS ${viewport.name}: ${name}`);
    };
    const dashboardReady = async () => {
      await expect(page.locator('.recommend-card')).toHaveCount(3);
      await expect(page.locator('.chart-panel [data-testid="price-chart"] canvas').first()).toBeVisible();
      await expect(page.locator('.skeleton')).toHaveCount(0);
      await paint(page);
    };
    const navigate = async label => {
      if (viewport.name === 'mobile') {
        await page.getByRole('button', { name: '메뉴 열기', exact: true }).click();
        await expect(page.locator('.sidebar')).toHaveClass(/is-open/);
      }
      await page.locator('.sidebar').getByRole('button', { name: label, exact: false }).click();
    };

    try {
      await check('dashboard loads exactly three hydrated recommendations and a chart', async () => {
        await page.goto(baseURL, { waitUntil: 'domcontentloaded' });
        await dashboardReady();
        await expect(page.getByRole('heading', { name: /시장의 흐름을/ })).toBeVisible();
        await expect(page.locator('.error-banner')).toHaveCount(0);
        await expect(page.locator('.metric-card')).toHaveCount(4);
      });
      await check('each recommendation has a numeric price and two concrete reasons', async () => {
        for (const card of await page.locator('.recommend-card').all()) {
          await expect(card.locator('.recommend-price strong')).toHaveText(/\d[\d,]+원/);
          await expect(card.locator('.entry-zone strong')).toHaveText(/\d[\d,]+\s*~\s*\d[\d,]+/);
          await expect(card.locator('.recommend-reasons p')).toHaveCount(2);
          for (const reason of await card.locator('.recommend-reasons p').all()) assert.ok((await reason.innerText()).trim().length > 5);
        }
      });
      await check('dashboard fits the viewport and saves a screenshot', async () => {
        await noOverflow(page, viewport.width);
        await page.screenshot({ path: join(artifacts, `${viewport.name}-dashboard.png`), fullPage: true });
      });
      const opening = page.locator('.opening-radar');
      await check('v1.2 separates three roles and withholds unconnected current rankings', async () => {
        await expect(opening.locator('.or-loading')).toHaveCount(0);
        await expect(opening.locator('.or-list')).toHaveCount(3);
        await expect(opening.locator('.or-rank-flow')).toHaveCount(0);
        await expect(opening.locator('.or-rank-entry')).toHaveCount(0);
        await expect(opening.getByText('뉴스 미연결은 시장에 재료가 없다는 뜻이 아닙니다.', { exact: false })).toBeVisible();
      });
      await check('v1.2 replay promotes a domestic measured theme and keeps entries separate', async () => {
        await opening.getByRole('button', { name: 'DEMO 리플레이', exact: false }).click();
        await expect(opening.locator('.or-rank-hypothesis')).toHaveCount(3);
        await expect(opening.locator('.or-rank-flow')).toHaveCount(3);
        await expect(opening.locator('.or-rank-flow h4').first()).toHaveText('국내 정책 재료 · 합성');
        await expect(opening.locator('.or-rank-entry')).toHaveCount(0);
        await expect(opening.locator('.or-replay-banner')).toContainText('실제 당일 뉴스·가격·거래대금이 아닙니다.');
        await opening.locator('.or-rank-flow details').first().locator('summary').click();
        await expect(opening.locator('.or-rank-flow').first()).toContainText('순매수');
        await expect(opening.locator('.or-catalyst-meta').first()).toContainText('첫 수신');
        await expect(opening.locator('.or-transition').first()).toBeVisible();
        await noOverflow(page, viewport.width);
        await page.screenshot({ path: join(artifacts, `${viewport.name}-opening-replay.png`), fullPage: true });
      });
      await check('v1.2 discovery withholds confirmation before sufficient opening windows', async () => {
        await opening.getByRole('combobox', { name: '개장 리플레이 기준 시각', exact: true }).selectOption('09:02');
        await expect(opening.locator('.or-loading')).toHaveCount(0);
        await expect(opening.locator('.or-rank-flow')).toHaveCount(0);
        await expect(opening.locator('.or-rank-hypothesis')).toHaveCount(3);
      });
      await check('v1.2 reverse rotation changes the measured leader after new observations', async () => {
        await opening.getByRole('combobox', { name: '개장 리플레이 시나리오', exact: true }).selectOption('reverse_rotation');
        await opening.getByRole('combobox', { name: '개장 리플레이 기준 시각', exact: true }).selectOption('09:20');
        await expect(opening.locator('.or-rank-flow h4').first()).toHaveText('미국 선행 광통신 · 합성');
      });
      await check('v1.2 feed outage clears measured and entry rankings', async () => {
        await opening.getByRole('combobox', { name: '개장 리플레이 시나리오', exact: true }).selectOption('data_outage');
        await expect(opening.locator('.or-loading')).toHaveCount(0);
        await expect(opening.locator('.or-rank-flow')).toHaveCount(0);
        await expect(opening.locator('.or-rank-entry')).toHaveCount(0);
      });
      await check('v1.2 current view does not retain synthetic leaders from replay', async () => {
        await opening.getByRole('button', { name: '관측 모니터', exact: true }).click();
        await expect(opening.locator('.or-loading')).toHaveCount(0);
        await expect(opening.locator('.or-rank-flow')).toHaveCount(0);
        await expect(opening.locator('.or-rank-hypothesis')).toHaveCount(0);
        await expect(opening.locator('.or-replay-banner')).toHaveCount(0);
      });
      await check('theme selection filters recommendations and reset restores three', async () => {
        const cards = page.locator('.theme-card');
        await expect(cards).toHaveCount(5);
        await cards.first().click();
        await expect(cards.first()).toHaveAttribute('aria-pressed', 'true');
        await expect(page.locator('.filter-caption')).toBeVisible();
        const filtered = await page.locator('.recommend-card').count();
        assert.ok(filtered > 0 && filtered < 3, `Expected a narrowed theme result, got ${filtered}`);
        await page.getByRole('button', { name: '필터 해제', exact: false }).click();
        await expect(page.locator('.recommend-card')).toHaveCount(3);
        await expect(page.locator('.filter-caption')).toHaveCount(0);
      });
      await check('all candidates expands to six and returns to the qualified top three', async () => {
        await page.getByRole('button', { name: '전체 후보 보기', exact: false }).click();
        await expect(page.locator('.recommend-card')).toHaveCount(6);
        await page.getByRole('button', { name: 'A등급 이상 보기', exact: false }).click();
        await expect(page.locator('.recommend-card')).toHaveCount(3);
      });

      const firstName = (await page.locator('.recommend-name span').first().innerText()).trim();
      const stocksResponse = await context.request.get(`${isolatedApi.origin}/api/stocks`);
      const stocks = (await stocksResponse.json()).items;
      const stock = stocks.find(stock => stock.name === firstName);
      assert.ok(stock, `Recommendation ${firstName} must exist in the API`);

      await check('star adds a stock with reload persistence', async () => {
        await page.getByRole('button', { name: `${firstName} 관심종목 추가`, exact: true }).click();
        await expect(page.getByRole('button', { name: `${firstName} 관심종목 해제`, exact: true })).toBeVisible();
        await page.reload({ waitUntil: 'domcontentloaded' });
        await dashboardReady();
        await expect(page.getByRole('button', { name: `${firstName} 관심종목 해제`, exact: true })).toBeVisible();
        assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('flowtrade-watchlist'))), [stock.symbol]);
      });
      await check('star removal persists after reload', async () => {
        await page.getByRole('button', { name: `${firstName} 관심종목 해제`, exact: true }).click();
        await page.reload({ waitUntil: 'domcontentloaded' });
        await dashboardReady();
        await expect(page.getByRole('button', { name: `${firstName} 관심종목 추가`, exact: true })).toBeVisible();
        assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('flowtrade-watchlist'))), []);
      });
      await check('stock search opens the correct stock route and analysis', async () => {
        await page.getByRole('textbox', { name: '종목 검색', exact: true }).fill(stock.symbol);
        await expect(page.locator('.search-results button')).toHaveCount(1);
        await page.locator('.search-results button').click();
        await expect(page).toHaveURL(new RegExp(`/stocks/${stock.symbol}$`));
        await expect(page.getByRole('dialog', { name: '종목 상세 분석' }).getByRole('heading', { name: firstName, exact: false })).toBeVisible();
      });
      const dialog = page.getByRole('dialog', { name: '종목 상세 분석' });
      await check('detail shows MA5, technical stop, both targets, and recommendation reasons', async () => {
        await expect(dialog.getByText('MA5', { exact: false }).first()).toBeVisible();
        await expect(dialog.getByText('5일 이동평균', { exact: true })).toBeVisible();
        await expect(dialog.getByText('기술적 손절', { exact: true })).toBeVisible();
        await expect(dialog.getByText('1차 목표', { exact: true })).toBeVisible();
        await expect(dialog.getByText('2차 목표', { exact: true })).toBeVisible();
        await expect(dialog.locator('.detail-reason')).toHaveCount(stock.reasons.length);
        for (const expected of [stock.technical.ma5, stock.risk.stop, ...stock.targets]) {
          await expect(dialog.getByText(`${new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 }).format(expected)}원`, { exact: true }).first()).toBeVisible();
        }
        await expect(dialog.locator('[data-testid="price-chart"] canvas').first()).toBeVisible();
        await noOverflow(page, viewport.width);
      });
      await check('daily, five-minute, and one-minute tabs change the chart timeframe', async () => {
        for (const [label, chartLabel] of [['5분', '5분봉'], ['1분', '1분봉'], ['일봉', '일봉']]) {
          const button = dialog.getByRole('button', { name: label, exact: true });
          await button.click();
          await expect(button).toHaveClass(/active/);
          await expect(dialog.getByRole('group', { name: new RegExp(chartLabel) })).toBeVisible();
          await expect(dialog.locator('[data-testid="price-chart"] canvas').first()).toBeVisible();
          await paint(page);
        }
      });
      await check('chart zoom changes the rendered candles and reset works', async () => {
        const chart = dialog.locator('[data-testid="price-chart"]');
        await chart.scrollIntoViewIfNeeded();
        await page.mouse.move(0, 0);
        await paint(page);
        const before = createHash('sha256').update(await chart.screenshot()).digest('hex');
        await dialog.getByRole('button', { name: '차트 확대', exact: true }).click();
        await page.mouse.move(0, 0);
        await paint(page);
        const after = createHash('sha256').update(await chart.screenshot()).digest('hex');
        assert.notEqual(before, after, 'Zoom must alter the canvas rendering');
        await dialog.getByRole('button', { name: '차트 축소', exact: true }).click();
        await dialog.getByRole('button', { name: '차트 초기화', exact: true }).click();
        await paint(page);
        await page.screenshot({ path: join(artifacts, `${viewport.name}-stock-detail.png`), fullPage: true });
        await page.getByRole('button', { name: '종목 상세 닫기', exact: true }).click();
        await expect(dialog).toHaveCount(0);
      });
      await check('marking notifications read updates the real isolated API and survives reload', async () => {
        const before = await (await context.request.get(`${isolatedApi.origin}/api/notifications`)).json();
        assert.ok(before.unread > 0, 'Fresh isolated API must have unread notifications');
        await page.getByRole('button', { name: `알림 ${before.unread}개`, exact: true }).click();
        await page.getByRole('button', { name: '모두 읽음', exact: false }).click();
        await expect(page.getByRole('button', { name: '알림 0개', exact: true })).toBeVisible();
        const after = await (await context.request.get(`${isolatedApi.origin}/api/notifications`)).json();
        assert.equal(after.unread, 0);
        assert.ok(after.items.every(item => item.read));
        await page.reload({ waitUntil: 'domcontentloaded' });
        await dashboardReady();
        await expect(page.getByRole('button', { name: '알림 0개', exact: true })).toBeVisible();
      });

      for (const view of [
        { nav: '관심종목', slug: 'watchlist', title: '나만의 관심 종목', ready: async () => expect(page.getByRole('heading', { name: '아직 관심 종목이 없어요', exact: true })).toBeVisible() },
        { nav: '테마 탐색', slug: 'themes', title: '돈이 모이는 테마', ready: async () => expect(page.locator('.wv-theme-card')).toHaveCount(5) },
        { nav: '전략 백테스트', slug: 'backtest', title: '전략 검증', ready: async () => { await expect(page.locator('.wv-metric')).toHaveCount(4); await expect(page.locator('.wv-metric strong').first()).toHaveText(/[1-9]\d*건/); } },
        { nav: '모의매매', slug: 'paper', title: '가상으로 먼저, 신중하게', ready: async () => { await expect(page.locator('.wv-buy-form')).toBeVisible(); await expect(page.getByRole('heading', { name: '첫 가상 매수를 시작해 보세요', exact: true })).toBeVisible(); } },
        { nav: '전략 설정', slug: 'settings', title: '나에게 맞는 분석 기준', ready: async () => { await expect(page.locator('.wv-settings-group input[type="number"]').first()).toBeVisible(); assert.ok(await page.locator('.wv-settings-group input[type="number"]').count() > 10); await expect(page.getByText('실제 주문 비활성화', { exact: true })).toBeVisible(); } },
      ]) {
        await check(`${view.slug} is reachable from navigation and loads without errors`, async () => {
          await navigate(view.nav);
          await expect(page).toHaveURL(new RegExp(`\\?view=${view.slug}$`));
          await expect(page.getByRole('heading', { name: view.title, exact: true })).toBeVisible();
          await view.ready();
          await expect(page.locator('.wv-loading')).toHaveCount(0);
          await expect(page.locator('.workspace-view [role="alert"]')).toHaveCount(0);
          await noOverflow(page, viewport.width);
        });
      }
      await check('dashboard navigation returns to the recommendations', async () => {
        await navigate('대시보드');
        await expect(page).toHaveURL(new RegExp('/$'));
        await dashboardReady();
      });
      await check('nonexistent stock resolves to an error instead of an indefinite spinner', async () => {
        await page.goto(`${baseURL}/stocks/999999`, { waitUntil: 'domcontentloaded' });
        const missingDialog = page.getByRole('dialog', { name: '종목 상세 분석' });
        await expect(missingDialog).toBeVisible();
        await expect(missingDialog.getByText('종목을 찾을 수 없습니다.', { exact: false })).toBeVisible();
        await expect(missingDialog.locator('.detail-loading')).toHaveCount(0);
        await expect(missingDialog.getByRole('button', { name: '종목 상세 닫기', exact: true })).toBeVisible();
        await noOverflow(page, viewport.width);
      });
      await check('no browser runtime errors or unexpected failed API calls', async () => {
        assert.deepEqual(errors, []);
        assert.deepEqual(failedApis, []);
      });
    } catch (error) {
      await page.screenshot({ path: join(artifacts, `${viewport.name}-failure.png`), fullPage: true }).catch(() => {});
      console.error(`FAIL ${viewport.name}; screenshots in ${artifacts}`);
      throw error;
    } finally {
      await context.close();
      await isolatedApi.stop();
    }
  }
  console.log(`\n${passed} browser checks passed. Screenshots: ${artifacts}`);
} finally {
  await browser.close();
}
