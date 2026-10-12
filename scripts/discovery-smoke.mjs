import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { chromium, expect as playwrightExpect } from '@playwright/test';

// Every API response below is constructed browser-test input. Names, prices,
// classifications and candles are NOT observed market data. No backend, OAuth,
// broker connection, order or persistent user data is accessed by this script.
// Serve the web app first, then: E2E_BASE_URL=http://127.0.0.1:10000 node scripts/discovery-smoke.mjs
const baseURL = process.env.E2E_BASE_URL || 'http://127.0.0.1:10000';
const artifacts = process.env.E2E_ARTIFACT_DIR || '/tmp/flowtrade-discovery-e2e';
const executablePath = process.env.CHROMIUM_PATH || '/usr/bin/chromium';
const timeout = 20_000;
const expect = playwrightExpect.configure({ timeout });
const targetSymbol = '0101N0';
const fixtureSource = 'https://example.com/constructed-discovery-fixture';
const results = [];
let passed = 0;
await mkdir(artifacts, { recursive: true });

const stamp = (offset = 0) => new Date(Date.now() + offset).toISOString();
const json = (route, value) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(value) });
const factor = { value: null, points: null, max_points: 15, quality: 'UNAVAILABLE' };
const emptyRank = { status: 'UNAVAILABLE', items: [], reason: 'Constructed UI fixture; no feed attached.' };

function themes() {
  return [{
    id: 'sector:100', name: '예상 테마 A · 합성 테스트', state: 'WATCH', quality: 'ANALYSIS_ONLY',
    score: 62, available_max_points: 70, factor_coverage: .7, flow_confirmed: false,
    coverage: { members_expected: 49, members_observed: 5, quote_fresh_count: 1 },
    factors: { us_1d: factor, domestic_breadth: { value: .6, points: 12, max_points: 20, quality: 'ANALYSIS_ONLY' } },
    evidence: [{ kind: 'OFFICIAL_SECTOR_CLASSIFICATION', title: '산업 분류 근거 · 합성 UI 응답',
      relationship: 'OFFICIAL_SECTOR_MEMBER', source_url: fixtureSource, provider_at: null, first_seen_at: stamp(-60_000) }],
    recommended_stocks: [{ symbol: targetSymbol, name: '미등록 코드 UI 테스트 · 합성', price: null, quote: null,
      watch_score: { score: 24, coverage: .3 }, technical_quality: 'UNAVAILABLE', association_kind: 'OFFICIAL_SECTOR_MEMBER' }],
  }, {
    id: 'sector:200', name: '관측 테마 B · 합성 테스트', state: 'WATCH', quality: 'OBSERVED', score: 38,
    observation_kind: 'OBSERVED_DAILY_TRADING_AMOUNT', provider_at: stamp(), flow_confirmed: false,
    coverage: { members_expected: 48, members_observed: 7, quote_fresh_count: 1 },
    turnover: { allocated_trading_amount_krw: 300_000_000_000, observed_unique_denominator_krw: 400_000_000_000 },
    evidence: [{ kind: 'OBSERVED_TURNOVER', title: '누적 거래대금 근거 · 합성 UI 응답', source_url: fixtureSource,
      provider_at: stamp(), first_seen_at: stamp() }],
    recommended_stocks: [],
  }];
}

function overview(mode) {
  const items = themes();
  const unavailable = mode === 'unavailable';
  return { as_of: stamp(), source: mode === 'synthetic' ? 'synthetic' : 'toss', status: unavailable ? 'UNAVAILABLE' : 'PARTIAL',
    universe: { expected: unavailable ? null : 128, observed: unavailable ? 0 : 12,
      classified: unavailable ? 0 : 96, unclassified: unavailable ? 0 : 32, quote_fresh: unavailable ? 0 : 2,
      missing: unavailable ? 0 : 116, stale: 0, bootstrap_observed: unavailable ? 0 : 1,
      status: unavailable ? 'UNAVAILABLE' : 'PARTIAL', markets: unavailable ? [] : ['KOSPI', 'KOSDAQ'],
      scope: '합성 테스트: 확인된 목록 128개와 초기 랭킹 관측 1개는 서로 다른 범위입니다.' },
    theme_hypotheses_top3: unavailable ? [] : [items[0]], observed_theme_leaders: unavailable ? [] : [items[1]],
    themes: unavailable ? [] : items, unmapped_leaders: [], news_status: 'UNAVAILABLE',
    measurement_definition: '합성 UI 검증용 응답 · 실제 시장 관측 아님 · 누적 거래대금과 순매수는 별도', reason_codes: [], orders_enabled: false };
}

function stockRows() {
  const common = { source: 'toss', quote_status: 'UNAVAILABLE', quote_at: null, price: null,
    turnover_quality: 'UNAVAILABLE', observed_turnover_krw: null, analysis_status: 'PENDING' };
  const catalog = Array.from({ length: 128 }, (_, index) => ({ ...common, symbol: String(700000 + index),
    name: `합성 국내 종목 ${String(index + 1).padStart(3, '0')}`, market: 'KOSDAQ', catalog_member: true,
    sector_ids: index < 96 ? [index % 2 === 0 ? '100' : '200'] : [],
    theme_refs: index < 96 ? [{ id: index % 2 === 0 ? 'sector:100' : 'sector:200',
      name: index % 2 === 0 ? '예상 테마 A · 합성 테스트' : '관측 테마 B · 합성 테스트',
      relationship: 'OFFICIAL_SECTOR_MEMBER', source_url: fixtureSource }] : [], watch_score: index < 96 ? 10 : null }));
  return [{ ...common, symbol: targetSymbol, name: '미등록 코드 UI 테스트 · 합성', market: 'KOSPI',
    catalog_member: false, price: 24500, quote_at: stamp(), quote_status: 'LIVE', watch_score: 24,
    sector_ids: ['100'], theme_refs: [{ id: 'sector:100', name: '예상 테마 A · 합성 테스트',
      relationship: 'OFFICIAL_SECTOR_MEMBER', source_url: fixtureSource }] }, ...catalog];
}

function stockPage(url, mode) {
  let items = mode === 'unavailable' ? [] : stockRows();
  const theme = url.searchParams.get('theme_id');
  const query = (url.searchParams.get('q') || '').toLowerCase();
  if (theme) items = items.filter(row => row.theme_refs.some(item => item.id === theme));
  if (url.searchParams.get('unclassified') === 'true') items = items.filter(row => !row.sector_ids.length);
  if (query) items = items.filter(row => `${row.name} ${row.symbol}`.toLowerCase().includes(query));
  const page = Number(url.searchParams.get('page') || 1);
  const limit = Number(url.searchParams.get('limit') || 25);
  return { as_of: stamp(), source: 'toss', status: mode === 'unavailable' ? 'UNAVAILABLE' : 'PARTIAL',
    items: items.slice((page - 1) * limit, page * limit), total: items.length, page, limit,
    scope: '합성 검증 목록 · 128개 정식 목록 + 초기 관측 1개' };
}

function candles(interval) {
  const origin = interval === 'daily' ? Date.parse('2026-09-01T00:00:00Z') : Date.now() - 3_600_000;
  const count = interval === 'daily' ? 24 : interval === 'five' ? 6 : 12;
  const step = interval === 'daily' ? 86_400_000 : interval === 'five' ? 300_000 : 60_000;
  return Array.from({ length: count }, (_, index) => {
    const time = new Date(origin + index * step).toISOString();
    return { time: interval === 'daily' ? time.slice(0, 10) : time,
      ...(interval === 'daily' ? {} : { start: time, end: new Date(origin + (index + 1) * step).toISOString() }),
      open: 24000 + index * 10, high: 24120 + index * 10, low: 23920 + index * 10, close: 24050 + index * 10,
      volume: 1000 + index * 20, source: 'toss', venue: 'CONSOLIDATED', ma5: null, ma5_5m: null,
      status: index === count - 1 ? 'PARTIAL' : 'CLOSED' };
  });
}

function detail(mode) {
  const ready = mode !== 'pending';
  const stale = mode === 'stale';
  return { symbol: targetSymbol, name: '미등록 코드 UI 테스트 · 합성', source: mode === 'synthetic' ? 'synthetic' : 'toss',
    as_of: stamp(), status: ready ? 'READY' : 'PENDING', reason: '합성 UI 캐시 응답 · 실제 시장 분석이 아닙니다.',
    quote: { price: 24500, quote_at: stamp(stale ? -30_000 : 0), first_seen_at: stamp(), status: 'LIVE', latency_seconds: 0 },
    chart: { daily: ready ? candles('daily') : [], minute: ready ? candles('minute') : [], five_minute: ready ? candles('five') : [],
      levels: { ma5: null, pivot: null, stop: null, target1: null, target2: null } },
    technical: null, theme: themes()[0], quality: { quote: 'LIVE', daily: ready ? 'READY' : 'UNAVAILABLE',
      minute: ready ? 'READY' : 'UNAVAILABLE', five_minute: ready ? 'PARTIAL' : 'UNAVAILABLE', entry: 'UNAVAILABLE' },
    earlyEntry: { state: stale || mode === 'synthetic' ? 'EARLY_ENTRY_READY' : 'UNAVAILABLE',
      quality: stale || mode === 'synthetic' ? 'LIVE' : 'UNAVAILABLE', eligible: stale || mode === 'synthetic',
      MA5_DAILY: null, MA5_5M: null, risk: null },
    watch_score: { score: 24, coverage: .3 }, reasons: ['INSUFFICIENT_COMPLETED_DAILY_BARS'], warnings: [], orders_enabled: false };
}

async function noOverflow(page, width) {
  const dimensions = await page.evaluate(() => ({ document: document.documentElement.scrollWidth, body: document.body.scrollWidth }));
  assert.ok(dimensions.document <= width + 1 && dimensions.body <= width + 1,
    `Horizontal page overflow at ${width}px: ${JSON.stringify(dimensions)}`);
  const bounds = await page.getByTestId('market-discovery').boundingBox();
  assert.ok(bounds && bounds.x >= -1 && bounds.x + bounds.width <= width + 1, 'Market discovery exceeds the viewport');
}

const browser = await chromium.launch({ executablePath, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
try {
  for (const viewport of [{ name: 'desktop', width: 1440, height: 1000 }, { name: 'mobile', width: 390, height: 844 }]) {
    const context = await browser.newContext({ viewport, locale: 'ko-KR', timezoneId: 'Asia/Seoul', reducedMotion: 'reduce' });
    const page = await context.newPage();
    page.setDefaultTimeout(timeout);
    const state = { overview: 'unavailable', detail: 'pending', failOverview: false };
    const errors = [], consoleErrors = [], expectedNetworkErrors = [], unexpectedApis = [], mutations = [], requests = [];
    const expectedAbortUrls = new Set();
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => {
      if (message.type() !== 'error') return;
      const location = message.location().url;
      if (/Failed to load resource/.test(message.text()) && (expectedAbortUrls.has(location) || state.detail === 'error' || state.failOverview)) {
        expectedNetworkErrors.push(message.text());
      } else consoleErrors.push(message.text());
    });
    await context.addInitScript(() => localStorage.setItem('flowtrade-watchlist', '[]'));
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      return url.origin === new URL(baseURL).origin ? route.continue() : route.abort('blockedbyclient');
    });
    await context.route('**/api/**', async route => {
      const request = route.request(), url = new URL(request.url());
      requests.push({ path: url.pathname + url.search, method: request.method(), at: Date.now() });
      if (request.method() !== 'GET') { mutations.push(url.pathname); return json(route, { detail: 'No mutations permitted in discovery UI test.' }); }
      if (url.pathname === '/api/discovery/overview') {
        if (state.failOverview) { expectedAbortUrls.add(request.url()); return route.abort('failed'); }
        return json(route, overview(state.overview));
      }
      if (url.pathname === '/api/discovery/stocks') return json(route, stockPage(url, state.overview));
      if (url.pathname === `/api/discovery/stocks/${targetSymbol}`) {
        if (state.detail === 'error') { expectedAbortUrls.add(request.url()); return route.abort('failed'); }
        return json(route, detail(state.detail));
      }
      if (url.pathname === '/api/market') return json(route, { mode: 'demo', asOf: stamp(), session: 'CONSTRUCTED_TEST',
        sessionLabel: '합성 테스트 · 실제 시장 아님', venueLabel: '합성 테스트', indices: [], risk: { level: 'UNKNOWN', label: '미평가' },
        usMarkets: [], dataStatus: {}, summary: '독립 시장 탐색 브라우저 검증 중' });
      if (['/api/stocks', '/api/themes', '/api/notifications'].includes(url.pathname)) return json(route, { items: [] });
      if (url.pathname === '/api/market/quotes') return json(route, { as_of: stamp(), source: 'toss', status: 'UNAVAILABLE', is_open: false,
        provider: 'MOCK · 테스트 시세 미제공', items: [], reason: 'Constructed UI test; no quotes.' });
      if (url.pathname === '/api/market/overview') return json(route, { as_of: stamp(), mode: 'unavailable', quality: 'UNAVAILABLE',
        premarket_hypothesis_top3: emptyRank, live_opening_flow_top3: emptyRank, entry_ready_top3: emptyRank,
        catalysts: [], rotation_history: [], data_freshness: {} });
      unexpectedApis.push(url.pathname);
      return route.fulfill({ status: 418, contentType: 'application/json', body: JSON.stringify({ detail: 'Unexpected API request in fully mocked discovery test.' }) });
    });
    const discovery = page.getByTestId('market-discovery');
    const refresh = () => discovery.getByRole('button', { name: '시장 탐색 자료 새로고침', exact: true }).click();
    const check = async (name, work) => {
      await work();
      assert.deepEqual(errors, [], 'Browser runtime errors');
      assert.deepEqual(consoleErrors, [], 'Unexpected browser console errors');
      assert.deepEqual(unexpectedApis, [], 'Unexpected or unmocked API requests');
      assert.deepEqual(mutations, [], 'Unexpected mutating API requests');
      assert.doesNotMatch(await discovery.innerText(), /\bNaN\b|\bundefined\b/, 'Invalid numeric/field values reached the UI');
      passed += 1;
      console.log(`PASS ${viewport.name}: ${name}`);
    };
    try {
      await check('unavailable feeds show no fabricated themes, stocks or chart', async () => {
        await page.goto(baseURL, { waitUntil: 'domcontentloaded' });
        await expect(discovery).toBeVisible();
        await expect(discovery.getByTestId('discovery-theme-card')).toHaveCount(0);
        await expect(discovery.getByTestId('discovery-stock-row')).toHaveCount(0);
        await expect(discovery.locator('.md-heading-meta')).toContainText('UNAVAILABLE');
        await expect(discovery.locator('[data-testid="price-chart"]')).toHaveCount(0);
      });
      await check('catalog denominator excludes bootstrap observations and list exceeds 100', async () => {
        state.overview = 'ready';
        await refresh();
        await expect(discovery.getByTestId('discovery-stock-row')).toHaveCount(25);
        await expect(discovery.locator('.md-coverage-grid > div').first()).toContainText('128개');
        await expect(discovery.locator('.md-coverage-note')).toContainText('초기 랭킹 관측 1개 (목록 분모 제외)');
        await expect(discovery.locator('.md-stock-caption')).toContainText('129개');
        const bootstrap = discovery.locator(`[data-testid="discovery-stock-row"][data-symbol="${targetSymbol}"]`);
        await expect(bootstrap).toHaveAttribute('data-catalog-member', 'false');
        await expect(bootstrap).toContainText('정식 목록 확인 대기');
      });
      await check('hypothesis priority and observed turnover use different metrics and sources', async () => {
        const hypothesis = discovery.locator('[data-testid="discovery-theme-card"][data-kind="hypothesis"]');
        const observed = discovery.locator('[data-testid="discovery-theme-card"][data-kind="observed"]');
        await expect(hypothesis).toHaveCount(1);
        await expect(observed).toHaveCount(1);
        await expect(hypothesis).toContainText('예상 테마 A');
        await expect(observed).toContainText('관측 테마 B');
        await expect(hypothesis).toContainText('수익 확률 아님');
        await expect(hypothesis).toContainText('예상 관찰 시점 미평가');
        await expect(observed).toContainText('3,000억');
        await expect(observed).toContainText('랭킹 종목군 · 순매수 아님');
        await expect(observed.locator('.md-theme-title')).not.toContainText('LIVE');
        await hypothesis.locator('summary').click();
        await expect(hypothesis.getByRole('link')).toHaveAttribute('href', fixtureSource);
        await expect(hypothesis).toContainText('최초 확인');
        await expect(hypothesis).toContainText('시각 미제공');
        await expect(hypothesis).toContainText('Toss 산업 분류 소속');
        await expect(hypothesis).not.toContainText('직접 관계 확인');
        await noOverflow(page, viewport.width);
        await page.screenshot({ path: join(artifacts, `${viewport.name}-constructed-discovery-overview.png`), fullPage: true });
      });
      await check('theme selection finds an alphanumeric legacy-unregistered stock without legacy detail', async () => {
        await discovery.locator('[data-kind="hypothesis"]').getByRole('button', { name: '테마 종목 살펴보기', exact: false }).click();
        await expect(discovery.getByRole('combobox', { name: '시장 탐색 테마 필터', exact: true })).toHaveValue('sector:100');
        const row = discovery.locator(`[data-testid="discovery-stock-row"][data-symbol="${targetSymbol}"]`);
        await expect(row).toBeVisible();
        await row.getByRole('button').click();
        await expect(discovery.getByTestId('discovery-chart-status')).toHaveAttribute('data-status', 'PENDING');
        await expect(discovery.locator('.md-analysis [data-testid="price-chart"]')).toHaveCount(0);
        await expect(discovery.locator('.md-chart-empty')).toContainText('실제 차트 분석 대기');
        assert.ok(requests.some(item => item.path === `/api/discovery/stocks/${targetSymbol}?theme_id=sector%3A100`));
        assert.ok(!requests.some(item => item.path.startsWith(`/api/stocks/${targetSymbol}`)));
        assert.equal(new URL(page.url()).pathname, '/');
      });
      await check('five-second selection polling changes PENDING to READY using only actual-shaped cached candles', async () => {
        const initial = requests.filter(item => item.path.startsWith(`/api/discovery/stocks/${targetSymbol}`)).at(-1);
        state.detail = 'ready';
        await expect(discovery.getByTestId('discovery-chart-status')).toHaveAttribute('data-status', 'READY');
        await expect(discovery.locator('.md-analysis [data-testid="price-chart"] canvas').first()).toBeVisible();
        const refreshed = requests.filter(item => item.path.startsWith(`/api/discovery/stocks/${targetSymbol}`)).at(-1);
        assert.ok(refreshed.at - initial.at >= 3_500, 'READY arrived without the selected-detail polling interval');
        await expect(discovery.locator('.md-analysis')).toContainText('현재 진입 확정으로 표시하지 않습니다.');
        await expect(discovery.locator('.md-analysis')).toContainText('분석 캐시와 현재 진입 판단은 별도');
        await expect(discovery.locator('.md-analysis [role="group"][aria-label*="가격 및 거래량"]')).not.toContainText('진입 구간');
        await discovery.locator('.md-candle-details summary').click();
        await expect(discovery.getByTestId('discovery-chart-table').locator('tbody tr')).toHaveCount(8);
        await noOverflow(page, viewport.width);
        await page.screenshot({ path: join(artifacts, `${viewport.name}-constructed-discovery-ready.png`), fullPage: true });
      });
      await check('nullable levels and partial five-minute bars remain previews without invented prices', async () => {
        await discovery.getByRole('button', { name: '5분', exact: true }).click();
        await expect(discovery.locator('.md-chart-summary')).toContainText('미완성 1개');
        await expect(discovery.getByTestId('discovery-chart-table')).toContainText('미완성 · PREVIEW');
        await expect(discovery.locator('.md-analysis [role="group"][aria-label*="가격 및 거래량"]')).toContainText('MA5_5M · 이력 대기');
        await expect(discovery.locator('.md-analysis [role="group"][aria-label*="가격 및 거래량"]')).not.toContainText('MA5_DAILY');
        await discovery.getByRole('button', { name: '1분', exact: true }).click();
        await expect(discovery.locator('.md-chart-summary')).toContainText('실제 수신 12개 봉');
        await noOverflow(page, viewport.width);
      });
      await check('old quote time stays STALE despite fresh response and claimed entry-ready state', async () => {
        state.detail = 'stale';
        await discovery.getByRole('button', { name: '선택 종목 분석 다시 확인', exact: true }).click();
        await expect(discovery.getByTestId('discovery-chart-status')).toHaveAttribute('data-status', 'READY');
        await expect(discovery.getByTestId('discovery-chart-status').locator('.md-status-stale')).toHaveCount(1);
        await expect(discovery.locator('.md-analysis')).toContainText('현재 진입 확정으로 표시하지 않습니다.');
        await expect(discovery.locator('.md-analysis')).not.toContainText('서버의 현재 선진입 조건 판정');
      });
      await check('failed automatic detail refresh marks retained charts stale and withholds entry confirmation', async () => {
        state.detail = 'error';
        await expect(discovery.getByTestId('discovery-chart-status')).toHaveAttribute('data-status', 'STALE');
        await expect(discovery.locator('.md-analysis [data-testid="price-chart"] canvas').first()).toBeVisible();
        await expect(discovery.locator('.md-analysis-notice')).toContainText('갱신에 실패');
        await expect(discovery.locator('.md-analysis')).not.toContainText('서버의 현재 선진입 조건 판정');
        await page.screenshot({ path: join(artifacts, `${viewport.name}-constructed-discovery-stale.png`), fullPage: true });
      });
      await check('synthetic provenance never becomes an actual chart or hypothesis', async () => {
        state.detail = 'synthetic';
        await discovery.getByRole('button', { name: '선택 종목 분석 다시 확인', exact: true }).click();
        await expect(discovery.getByTestId('discovery-chart-status')).toHaveAttribute('data-status', 'UNAVAILABLE');
        await expect(discovery.locator('.md-analysis [data-testid="price-chart"]')).toHaveCount(0);
        await expect(discovery.getByTestId('discovery-chart-status')).toContainText('현재가 미제공');
        state.overview = 'synthetic';
        await refresh();
        await expect(discovery.getByTestId('discovery-theme-card')).toHaveCount(0);
        await expect(discovery.locator('.md-heading-meta')).toContainText('UNAVAILABLE');
        await discovery.getByRole('button', { name: '관찰 종료', exact: true }).click();
        state.overview = 'ready'; state.detail = 'ready';
        await refresh();
        await expect(discovery.getByTestId('discovery-theme-card')).toHaveCount(2);
      });
      await check('search covers alphanumeric codes and missing results without narrowing to the old 15 stocks', async () => {
        await discovery.getByRole('button', { name: '필터 해제', exact: true }).click();
        const search = discovery.getByRole('searchbox', { name: '시장 탐색 종목 검색', exact: true });
        await search.fill(targetSymbol);
        await expect(discovery.getByTestId('discovery-stock-row')).toHaveCount(1);
        await expect(discovery.getByTestId('discovery-stock-row')).toContainText(targetSymbol);
        await search.fill('목록에 없는 합성 검색어');
        await expect(discovery.getByTestId('discovery-stock-row')).toHaveCount(0);
        await expect(discovery.locator('.md-stocks')).toContainText('현재 조건의 검증된 종목이 없습니다.');
        await discovery.getByRole('button', { name: '필터 해제', exact: true }).click();
        await expect(discovery.getByTestId('discovery-stock-row')).toHaveCount(25);
      });
      await check('unclassified filter keeps unknown relationships visible and paginated', async () => {
        await discovery.getByRole('button', { name: '미분류만 보기', exact: false }).click();
        await expect(discovery.locator('.md-stock-caption')).toContainText('32개');
        await expect(discovery.getByTestId('discovery-stock-row')).toHaveCount(25);
        await expect(discovery.locator('.md-unmapped-label')).toHaveCount(25);
        await expect(discovery.getByTestId('discovery-stock-row').first()).toContainText('임의로 테마를 배정하지 않습니다.');
        await discovery.getByRole('button', { name: '시장 탐색 다음 페이지', exact: true }).click();
        await expect(discovery.getByTestId('discovery-stock-row')).toHaveCount(7);
        await expect(discovery.locator('.md-pagination')).toContainText('2 / 2');
        await noOverflow(page, viewport.width);
        await page.screenshot({ path: join(artifacts, `${viewport.name}-constructed-discovery-unclassified.png`), fullPage: true });
      });
      await check('server pagination reaches known catalog symbols beyond the Top 100 cutoff', async () => {
        await discovery.getByRole('button', { name: '필터 해제', exact: true }).click();
        await expect(discovery.locator('.md-pagination')).toContainText('1 / 6');
        for (let target = 2; target <= 6; target += 1) {
          const response = page.waitForResponse(value => {
            const url = new URL(value.url());
            return url.pathname === '/api/discovery/stocks' && url.searchParams.get('page') === String(target) && !url.searchParams.get('unclassified');
          });
          await discovery.getByRole('button', { name: '시장 탐색 다음 페이지', exact: true }).click();
          await response;
          await expect(discovery.locator('.md-pagination')).toContainText(`${target} / 6`);
          await expect(discovery.getByTestId('discovery-stock-row').first()).toHaveAttribute('data-symbol', String(700000 + (target - 1) * 25 - 1));
        }
        await expect(discovery.getByTestId('discovery-stock-row')).toHaveCount(4);
        await expect(discovery.getByTestId('discovery-stock-row').last()).toHaveAttribute('data-symbol', '700127');
        await expect(discovery.getByRole('button', { name: '시장 탐색 다음 페이지', exact: true })).toBeDisabled();
      });
      await check('overview failure removes current rankings, retains explicit scope and offers retry', async () => {
        state.failOverview = true;
        await refresh();
        await expect(discovery.locator('.md-error')).toContainText('갱신에 실패');
        await expect(discovery.getByTestId('discovery-theme-card')).toHaveCount(0);
        await expect(discovery.locator('.md-heading-meta')).toContainText('STALE');
        state.failOverview = false;
        await discovery.locator('.md-error').getByRole('button', { name: '다시 확인', exact: true }).click();
        await expect(discovery.getByTestId('discovery-theme-card')).toHaveCount(2);
        await expect(discovery.locator('.md-error')).toHaveCount(0);
        await noOverflow(page, viewport.width);
      });
      results.push({ viewport: viewport.name, runtimeErrors: errors, consoleErrors, expectedNetworkErrors,
        APIRequests: requests.length, mutationRequests: mutations.length, mocked: true });
    } catch (error) {
      await page.screenshot({ path: join(artifacts, `${viewport.name}-FAILED-constructed-fixture.png`), fullPage: true }).catch(() => {});
      throw error;
    } finally {
      await context.close();
    }
  }
} finally {
  await browser.close();
  await writeFile(join(artifacts, 'report.json'), JSON.stringify({ constructedBrowserFixtures: true, observedMarketData: false, passed, results }, null, 2) + '\n');
}
console.log(`\n${passed} discovery browser checks passed with constructed fixtures. Artifacts: ${artifacts}`);
