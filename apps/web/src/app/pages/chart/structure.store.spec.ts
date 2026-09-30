import { TestBed } from '@angular/core/testing';
import type { EngineEvent, Job } from '@trader/api-client';
import { of } from 'rxjs';
import { API_CLIENT } from '../../core/api/api';
import { JobsService } from '../../core/jobs/jobs';
import { StructureStore } from './structure.store';

const ok = (data: unknown) =>
  Promise.resolve({ data, response: { status: 200 } });

const job = (id: number, result: Record<string, unknown> = {}): Job =>
  ({ id, status: 'succeeded', result }) as unknown as Job;

const swing = (
  seq: number,
  status: EngineEvent['status'],
  type: string,
  price: number,
  at: string,
): EngineEvent => ({
  seq,
  kind: 'swing',
  status,
  payload: { type, price, timestamp: at },
  detected_at: at,
  confirmed_at: null,
  available_at: at,
  revises: null,
});

interface Request {
  params?: { query?: Record<string, unknown> };
  body?: {
    type?: string;
    params?: { engine?: string; timeframe?: string };
  } & Record<string, unknown>;
}
type Call = [string, Request?];
const calls = (mock: ReturnType<typeof vi.fn>) =>
  mock.mock.calls as unknown as Call[];

function setup() {
  let next = 0;
  const client = {
    GET: vi.fn((path: string) => {
      if (path === '/engine-runs/{run_id}/events') {
        return ok([
          swing(0, 'confirmed', 'high', 110, '2026-09-28T04:00:00Z'),
          swing(1, 'detected', 'low', 100, '2026-09-28T05:00:00Z'),
        ]);
      }
      if (path === '/level-zones') {
        return ok({ zones: [] });
      }
      return ok([]);
    }),
    POST: vi.fn((path: string) => {
      if (path === '/jobs') {
        return ok(job(++next, { run_id: 40 + next }));
      }
      return ok({});
    }),
    DELETE: vi.fn(() => Promise.resolve({ response: { status: 204 } })),
  };
  const jobs = {
    watch: vi.fn((id: number) => of(job(id, { run_id: 40 + id }))),
  };
  TestBed.configureTestingModule({
    providers: [
      StructureStore,
      { provide: API_CLIENT, useValue: client },
      { provide: JobsService, useValue: jobs },
    ],
  });
  return { client, jobs, store: TestBed.inject(StructureStore) };
}

const CONTEXT = { root_id: 1, chartTimeframe: '15m' };

describe('StructureStore', () => {
  it('без включённых слоёв ничего не запрашивает', async () => {
    const { store, client } = setup();

    await store.refresh(CONTEXT);

    expect(client.POST).not.toHaveBeenCalled();
    expect(store.overlay().markers).toEqual([]);
  });

  it('включение слоя запускает engine.run один раз на серию и TF и читает события as-of', async () => {
    const { store, client } = setup();
    await store.refresh({ ...CONTEXT, asOf: '2026-09-28T06:00:00Z' });

    await store.setLayer('swings', true);
    await store.setLayer('zigzag', true);

    const posted = calls(client.POST).filter(([path]) => path === '/jobs');
    expect(posted).toHaveLength(1);
    expect(posted[0]?.[1]?.body).toEqual({
      type: 'engine.run',
      params: { engine: 'zigzag', timeframe: '15m', root_id: 1 },
    });
    const query = calls(client.GET).at(-1)?.[1]?.params?.query;
    expect(query).toMatchObject({
      view: 'current',
      as_of: '2026-09-28T06:00:00Z',
    });
    expect(store.overlay().markers).toHaveLength(2);
    expect(store.overlay().segments).toHaveLength(1);
  });

  it('смена TF запускает прогон заново, recompute сбрасывает кэш', async () => {
    const { store, client } = setup();
    await store.refresh(CONTEXT);
    await store.setLayer('swings', true);

    await store.refresh({ root_id: 1, chartTimeframe: '1h' });
    await store.recompute();

    const timeframes = calls(client.POST).map(
      ([, r]) => r?.body?.params?.timeframe,
    );
    expect(timeframes).toEqual(['15m', '1h', '1h']);
  });

  it('зоны: прогоны levels по chart TF и выбранным старшим TF', async () => {
    const { store, client } = setup();
    await store.refresh(CONTEXT);
    await store.setZoneSources(['1h']);

    await store.setLayer('zones', true);

    const engines = calls(client.POST).map(
      ([, r]) => `${r?.body?.params?.engine}:${r?.body?.params?.timeframe}`,
    );
    expect(engines.sort()).toEqual(['levels:15m', 'levels:1h']);
    const zones = calls(client.GET).find(([path]) => path === '/level-zones');
    expect(zones?.[1]?.params?.query).toMatchObject({
      chart_timeframe: '15m',
      source_timeframes: ['15m', '1h'],
      root_id: 1,
    });
  });

  it('ошибка задачи показывается, график не ломается', async () => {
    const { store, jobs } = setup();
    jobs.watch.mockReturnValue(
      of({ id: 1, status: 'failed', error: 'нет баров' } as unknown as Job),
    );
    await store.refresh(CONTEXT);

    await store.setLayer('swings', true);

    expect(store.error()).toBe('нет баров');
    expect(store.overlay().markers).toEqual([]);
  });

  it('ручная сетка: две точки сохраняют сетку и выключают режим', async () => {
    const { store, client } = setup();
    await store.refresh({ contract_id: 7, chartTimeframe: '15m' });
    await store.pickPoint({ time: 1, price: 1 }); // режим выключен — игнор
    expect(store.pending()).toBeNull();

    store.toggleManual();
    await store.pickPoint({ time: 1_790_000_000, price: 100 });
    expect(store.pending()).toEqual({ time: 1_790_000_000, price: 100 });
    await store.pickPoint({ time: 1_790_003_600, price: 120 });

    const post = calls(client.POST).find(([path]) => path === '/fib-grids');
    expect(post?.[1]?.body).toEqual({
      contract_id: 7,
      timeframe: '15m',
      start: { time: new Date(1_790_000_000_000).toISOString(), price: 100 },
      end: { time: new Date(1_790_003_600_000).toISOString(), price: 120 },
    });
    expect(store.manualMode()).toBe(false);
    expect(store.pending()).toBeNull();
  });

  it('в replay ручные сетки, нарисованные позже as_of, скрыты', async () => {
    const { store, client } = setup();
    client.GET.mockImplementation((path: string) =>
      ok(
        path === '/fib-grids'
          ? [
              {
                id: 1,
                start: { time: '2026-09-28T04:00:00Z', price: 100 },
                end: { time: '2026-09-28T08:00:00Z', price: 120 },
                retracement: { '50': 110 },
                extension: {},
              },
            ]
          : [],
      ),
    );
    await store.refresh({
      root_id: 1,
      chartTimeframe: '15m',
      asOf: '2026-09-28T06:00:00Z',
    });

    await store.setLayer('fibonacci', true);
    const early = store.overlay().segments.length;
    await store.refresh({
      root_id: 1,
      chartTimeframe: '15m',
      asOf: '2026-09-28T09:00:00Z',
    });

    expect(early).toBe(0);
    expect(store.overlay().segments.length).toBeGreaterThan(0);
  });

  it('выбор уровня переключается повторным кликом', () => {
    const { store } = setup();

    store.selectLevel(3);
    expect(store.selectedLevel()).toBe(3);
    store.selectLevel(3);
    expect(store.selectedLevel()).toBeNull();
  });

  it('паттерны: история событий четырёх движков, выбор и показ отменённых', async () => {
    const { store, client } = setup();
    const pattern = (id: number, state: string, at: string): EngineEvent => ({
      seq: id,
      kind: 'pattern',
      status: state === 'candidate' ? 'detected' : 'invalidated',
      payload: {
        id,
        pattern: 'double_top',
        direction: 'bearish',
        state,
        reason: state === 'invalidated' ? 'broken' : undefined,
        end: at,
        points: [{ role: 'top1', price: 10, ts: at, index: 1 }],
        line: { p1: 9, p2: 9, t1: at, t2: at },
        height: 1,
        target: null,
        features: {},
        quality: { score: 50, components: {} },
      },
      detected_at: at,
      confirmed_at: null,
      available_at: at,
      revises: null,
    });
    client.GET.mockImplementation((path: string) =>
      ok(
        path === '/engine-runs/{run_id}/events'
          ? [
              pattern(1, 'candidate', '2026-09-28T04:00:00Z'),
              pattern(2, 'invalidated', '2026-09-28T05:00:00Z'),
            ]
          : [],
      ),
    );
    await store.refresh(CONTEXT);

    await store.setLayer('patterns', true);

    const engines = calls(client.POST).map(([, r]) => r?.body?.params?.engine);
    expect(engines.sort()).toEqual([
      'double_triple',
      'head_shoulders',
      'range_breakout',
      'trendlines',
    ]);
    const queries = calls(client.GET)
      .filter(([path]) => path === '/engine-runs/{run_id}/events')
      .map(([, r]) => r?.params?.query);
    expect(queries.every((q) => q?.['view'] === 'history')).toBe(true);
    expect(store.patterns()).toHaveLength(8); // по два в каждом из четырёх движков
    expect(store.shownPatterns().every((p) => p.state !== 'invalidated')).toBe(
      true,
    );
    expect(store.overlay().markers.length).toBeGreaterThan(0);

    store.setShowCancelled(true);
    expect(store.shownPatterns().some((p) => p.state === 'invalidated')).toBe(
      true,
    );
    store.selectPattern('double_triple:1');
    expect(store.selectedPatternInfo()?.id).toBe(1);
    store.selectPattern('double_triple:1');
    expect(store.selectedPattern()).toBeNull();
  });

  describe('блок «Исторически»', () => {
    const STATS = {
      matched: 3,
      unit: 'atr',
      buckets: [],
      warnings: [],
      skipped: 0,
    };

    const patternEvent = (): EngineEvent => ({
      seq: 0,
      kind: 'pattern',
      status: 'confirmed',
      payload: {
        id: 1,
        pattern: 'double_top',
        direction: 'bearish',
        state: 'confirmed',
        end: '2026-09-28T05:00:00Z',
        points: [
          {
            role: 'top1',
            price: 10,
            ts: '2026-09-28T04:00:00Z',
            index: 1,
          },
        ],
        line: { p1: 9, p2: 9, t1: null, t2: null },
        height: 1,
        target: 8,
        features: {},
        quality: { score: 50, components: {} },
      },
      detected_at: '2026-09-28T04:00:00Z',
      confirmed_at: '2026-09-28T05:00:00Z',
      available_at: '2026-09-28T05:00:00Z',
      revises: null,
    });

    const levelEvent = (role: string): EngineEvent => ({
      seq: 0,
      kind: 'level',
      status: 'detected',
      payload: {
        id: 5,
        source: 'swing_high',
        family: 'swing',
        price: 120,
        role,
        state: 'active',
        touches: 1,
        created_at: '2026-09-28T04:00:00Z',
        strength: { score: 60, version: 1, components: {} },
      },
      detected_at: '2026-09-28T04:00:00Z',
      confirmed_at: null,
      available_at: '2026-09-28T04:00:00Z',
      revises: null,
    });

    function withStats(events: EngineEvent[]) {
      const ctx = setup();
      ctx.client.GET.mockImplementation((path: string) => {
        if (path === '/stats/outcomes') {
          return ok(STATS);
        }
        return ok(path === '/engine-runs/{run_id}/events' ? events : []);
      });
      return ctx;
    }

    const statsCalls = (client: ReturnType<typeof setup>['client']) =>
      calls(client.GET).filter(([path]) => path === '/stats/outcomes');

    it('выбор паттерна запрашивает статистику типа и направления по его прогону', async () => {
      const { store, client } = withStats([patternEvent()]);
      await store.refresh({ ...CONTEXT, asOf: '2026-09-28T07:00:00Z' });
      await store.setLayer('patterns', true);

      store.selectPattern('double_triple:1');
      await vi.waitFor(() => expect(store.statsViews()[0]?.data).toBeTruthy());

      const [request] = statsCalls(client).slice(-1);
      expect(request?.[1]?.params?.query).toMatchObject({
        group: ['double_top'],
        direction: 'bearish',
        unit: 'atr',
        as_of: '2026-09-28T07:00:00Z',
      });
      const runs = calls(client.POST).filter(
        ([, r]) => r?.body?.params?.engine === 'double_triple',
      ).length;
      expect(runs).toBe(1);
      expect(store.statsViews()).toHaveLength(1);
      expect(store.statsViews()[0]).toMatchObject({
        pattern: true,
        loading: false,
        error: null,
      });
    });

    it('снятие выбора убирает блок, смена единиц перезапрашивает', async () => {
      const { store, client } = withStats([patternEvent()]);
      await store.refresh(CONTEXT);
      await store.setLayer('patterns', true);
      store.selectPattern('double_triple:1');
      await vi.waitFor(() => expect(store.statsViews()[0]?.data).toBeTruthy());

      store.setStatsUnit('pct');
      await vi.waitFor(() => expect(statsCalls(client)).toHaveLength(2));
      expect(statsCalls(client).at(-1)?.[1]?.params?.query).toMatchObject({
        unit: 'pct',
      });

      store.selectPattern('double_triple:1');
      expect(store.statsViews()).toEqual([]);
    });

    it('выбор уровня даёт отбой и пробой той же роли', async () => {
      const { store, client } = withStats([levelEvent('support')]);
      await store.refresh(CONTEXT);
      await store.setLayer('levels', true);

      store.selectLevel(5);
      await vi.waitFor(() => expect(statsCalls(client)).toHaveLength(2));

      const queries = statsCalls(client).map(([, r]) => r?.params?.query);
      expect(queries).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            group: ['level_touch'],
            direction: 'bullish',
          }),
          expect.objectContaining({
            group: ['level_break'],
            direction: 'bearish',
          }),
        ]),
      );
    });

    it('ошибка запроса показывается в блоке, без падения', async () => {
      const { store, client } = withStats([patternEvent()]);
      await store.refresh(CONTEXT);
      await store.setLayer('patterns', true);
      client.GET.mockImplementation((path: string) =>
        path === '/stats/outcomes'
          ? Promise.reject(new Error('сервер недоступен'))
          : ok(path === '/engine-runs/{run_id}/events' ? [patternEvent()] : []),
      );

      store.selectPattern('double_triple:1');
      await vi.waitFor(() =>
        expect(store.statsViews()[0]?.error).toBe('Нет связи с сервером'),
      );
    });

    it('выключение слоя очищает блок', async () => {
      const { store } = withStats([patternEvent()]);
      await store.refresh(CONTEXT);
      await store.setLayer('patterns', true);
      store.selectPattern('double_triple:1');
      await vi.waitFor(() => expect(store.statsViews()).toHaveLength(1));

      await store.setLayer('patterns', false);

      expect(store.statsViews()).toEqual([]);
    });
  });
});
