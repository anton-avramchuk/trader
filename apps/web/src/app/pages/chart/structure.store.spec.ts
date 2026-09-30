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
});
