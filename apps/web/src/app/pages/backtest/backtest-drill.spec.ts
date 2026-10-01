import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { provideTaiga } from '@taiga-ui/core';
import { provideEventPlugins } from '@taiga-ui/event-plugins';
import type { Backtest, BacktestTrade } from '@trader/api-client';
import { of } from 'rxjs';
import { API_CLIENT } from '../../core/api/api';
import { JobsService } from '../../core/jobs/jobs';
import type { LevelInfo } from '../chart/structure';
import {
  engineOf,
  engineRuns,
  legRows,
  mskDate,
  nearestLevel,
  replayLink,
} from './backtest-model';
import { Backtest as BacktestPage } from './backtest';
import { BacktestStore } from './backtest.store';

const ok = (data: unknown) =>
  Promise.resolve({ data, response: { status: 200 } });

const backtest = {
  id: 7,
  kind: 'single',
  status: 'succeeded',
  root_id: 3,
  timeframe_code: '15m',
  family: 'pattern',
  job_id: 70,
  params_hash: 'abcdef012345',
  period_from: '2020-10-01',
  period_to: '2020-10-31',
  versions: {
    engines: {
      levels: { run_id: 11 },
      double_triple: { run_id: 12 },
      head_shoulders: { run_id: 13 },
    },
  },
  result: { metrics: { ticks: { trades: 1 } } },
} as unknown as Backtest;

const trade = (overrides: Partial<BacktestTrade> = {}): BacktestTrade =>
  ({
    id: 1,
    segment: 'single',
    side: 'long',
    ref: 'double_triple:5',
    signal_price: 100,
    entry_time: '2020-10-05T21:30:00Z',
    exit_time: '2020-10-06T07:15:00Z',
    reason: 'target',
    gross_ticks: 12,
    cost_ticks: 2,
    mfe_ticks: 15,
    mae_ticks: -3,
    net_points: 0.1,
    net_rub: 80,
    legs: [
      {
        contract_id: 9,
        entry_time: '2020-10-05T21:30:00Z',
        exit_time: '2020-10-06T07:15:00Z',
        entry_price: 100.5,
        exit_price: 101.7,
        reason: 'target',
        gross_ticks: 12,
      },
    ],
    ...overrides,
  }) as BacktestTrade;

const level = (id: number, price: number, role = 'support'): LevelInfo => ({
  id,
  source: 'swing_low',
  family: 'swing',
  price,
  role: role as LevelInfo['role'],
  state: 'active',
  touches: 1,
  createdAt: '2020-10-01T00:00:00Z',
  score: 55,
  components: {},
  version: 1,
});

describe('модель drill-down', () => {
  it('прогоны и движок из ключа', () => {
    expect(engineRuns(backtest)).toEqual({
      levels: 11,
      double_triple: 12,
      head_shoulders: 13,
    });
    expect(engineRuns(null)).toEqual({});
    expect(engineOf('double_triple:5')).toBe('double_triple');
    expect(engineOf('levels:3:12')).toBe('levels');
    expect(engineOf(null)).toBeNull();
  });

  it('ближайший уровень и сторона цены', () => {
    const near = nearestLevel(
      [level(1, 90), level(2, 98.5), level(3, 120)],
      100,
    );

    expect(near?.level.id).toBe(2);
    expect(near?.above).toBe(true);
    expect(near?.distancePct).toBeCloseTo(1.5);
    expect(nearestLevel([level(1, 105)], 100)?.above).toBe(false);
    expect(nearestLevel([], 100)).toBeNull();
    expect(nearestLevel([level(1, 90)], null)).toBeNull();
  });

  it('дата МСК и ссылка на Replay', () => {
    expect(mskDate('2020-10-05T21:30:00Z')).toBe('2020-10-06'); // уже следующий день
    expect(mskDate('2020-10-05T10:00:00Z')).toBe('2020-10-05');
    expect(replayLink(backtest, trade())).toEqual({
      path: ['/replay'],
      queryParams: { root: 3, tf: '15m', date: '2020-10-06' },
    });
  });

  it('ноги сделки', () => {
    expect(legRows(trade())[0]).toEqual({
      contract: 9,
      entry: '2020-10-05 21:30',
      exit: '2020-10-06 07:15',
      entryPrice: '100.5000',
      exitPrice: '101.7000',
      reason: 'цель',
      ticks: '12.00',
    });
  });
});

interface Request {
  params?: { query?: Record<string, unknown>; path?: Record<string, unknown> };
}
type Call = [string, Request?];
const calls = (mock: ReturnType<typeof vi.fn>, path: string) =>
  (mock.mock.calls as unknown as Call[]).filter(([p]) => p === path);

function storeSetup(overrides: Record<string, () => Promise<unknown>> = {}) {
  const client = {
    GET: vi.fn((path: string) => {
      const custom = overrides[path];
      if (custom) {
        return custom();
      }
      switch (path) {
        case '/stats/occurrence':
          return ok({
            occurrence: {
              group: 'double_bottom',
              direction: 'bullish',
              quality: 71,
            },
            regime: 'uptrend/mid',
            outcomes: [],
          });
        case '/engine-runs/{run_id}/events':
          return ok([
            {
              seq: 1,
              kind: 'level',
              status: 'detected',
              payload: {
                id: 4,
                source: 'swing_low',
                family: 'swing',
                price: 98.5,
                role: 'support',
                state: 'active',
                touches: 1,
                created_at: '2020-10-01T00:00:00Z',
                strength: { score: 55, version: 1, components: {} },
              },
              detected_at: '2020-10-01T00:00:00Z',
              confirmed_at: null,
              available_at: '2020-10-01T00:00:00Z',
              revises: null,
            },
          ]);
        case '/forecast':
          return ok({
            empirical: {
              method: 'empirical',
              sample: 9,
              warnings: [],
              horizons: [],
            },
            knn: { method: 'knn', sample: 5, warnings: [], horizons: [] },
            thresholds: [0.5, 1, 2],
            warnings: [],
          });
        default:
          return ok([]);
      }
    }),
    POST: vi.fn(() => ok({})),
  };
  TestBed.configureTestingModule({
    providers: [
      BacktestStore,
      { provide: API_CLIENT, useValue: client },
      { provide: JobsService, useValue: { watch: () => of() } },
    ],
  });
  const store = TestBed.inject(BacktestStore);
  store.current.set(backtest);
  return { client, store };
}

describe('drill-down в сторе', () => {
  it('грузит вхождение, режим и ближайший уровень на момент входа', async () => {
    const { store, client } = storeSetup();

    await store.selectTrade(trade());

    const drill = store.drill();
    expect(drill?.loading).toBe(false);
    expect(drill?.occurrence?.regime).toBe('uptrend/mid');
    expect(drill?.level?.level.price).toBe(98.5);
    expect(drill?.level?.above).toBe(true);
    expect(drill?.levelsFound).toBe(1);
    const occurrence = calls(client.GET, '/stats/occurrence')[0]?.[1];
    expect(occurrence?.params?.query).toEqual({
      run_id: 12,
      key: 'double_triple:5',
      as_of: '2020-10-05T21:30:00Z',
    });
    const events = calls(client.GET, '/engine-runs/{run_id}/events')[0]?.[1];
    expect(events?.params?.path).toEqual({ run_id: 11 });
    expect(events?.params?.query).toMatchObject({
      view: 'current',
      as_of: '2020-10-05T21:30:00Z',
    });
  });

  it('прогноз — только по запросу, по всем прогонам и на момент входа', async () => {
    const { store, client } = storeSetup();
    await store.selectTrade(trade());
    expect(calls(client.GET, '/forecast')).toHaveLength(0);

    await store.loadForecast();

    const query = calls(client.GET, '/forecast')[0]?.[1]?.params?.query;
    expect(query).toMatchObject({
      query_run_id: 12,
      key: 'double_triple:5',
      unit: 'atr',
      as_of: '2020-10-05T21:30:00Z',
    });
    expect((query?.['run_id'] as number[]).sort()).toEqual([11, 12, 13]);
    expect(store.drill()?.forecast.data?.knn.sample).toBe(5);
    expect(store.drill()?.forecast.loading).toBe(false);
  });

  it('ошибки блоков не ломают панель и показываются заметками', async () => {
    const { store } = storeSetup({
      '/stats/occurrence': () => Promise.reject(new Error('нет события')),
      '/forecast': () => Promise.reject(new Error('нет связи')),
    });

    await store.selectTrade(trade());
    await store.loadForecast();

    const drill = store.drill();
    expect(drill?.occurrence).toBeNull();
    expect(drill?.notes).toContain('Событие: Нет связи с сервером');
    expect(drill?.level).not.toBeNull(); // уровни загрузились независимо
    expect(drill?.forecast.error).toBe('Нет связи с сервером');
  });

  it('без прогона движка в версиях — заметка и ошибка прогноза', async () => {
    const { store } = storeSetup();
    store.current.set({ ...backtest, versions: {} } as unknown as Backtest);

    await store.selectTrade(trade());
    await store.loadForecast();

    expect(store.drill()?.notes.length).toBe(2);
    expect(store.drill()?.forecast.error).toContain('не найден');
  });

  it('устаревший ответ не затирает новую сделку; закрытие очищает', async () => {
    let release: (value: unknown) => void = () => undefined;
    const slow = new Promise((resolve) => (release = resolve));
    let first_call = true;
    const { store } = storeSetup({
      '/stats/occurrence': () => {
        if (first_call) {
          first_call = false;
          return slow; // только первый запрос зависает
        }
        return ok({
          occurrence: { group: 'x' },
          regime: 'fresh',
          outcomes: [],
        });
      },
    });
    const first = store.selectTrade(trade({ id: 1 }));

    await store.selectTrade(trade({ id: 2, ref: 'levels:4:9' }));
    release(
      await ok({ occurrence: { group: 'x' }, regime: 'stale', outcomes: [] }),
    );
    await first;

    expect(store.drill()?.trade.id).toBe(2);
    expect(store.drill()?.occurrence?.regime).toBe('fresh');
    store.closeTrade();
    expect(store.drill()).toBeNull();
  });
});

describe('drill-down на странице', () => {
  async function settle(fixture: ComponentFixture<unknown>): Promise<void> {
    for (let i = 0; i < 5; i++) {
      await new Promise((resolve) => setTimeout(resolve));
      fixture.detectChanges();
    }
  }

  it('клик по сделке открывает детали со ссылкой на Replay и прогнозом по кнопке', async () => {
    const client = {
      GET: vi.fn((path: string) => {
        switch (path) {
          case '/roots':
            return ok([{ id: 3, code: 'NG' }]);
          case '/backtests':
            return ok([backtest]);
          case '/backtests/{experiment_id}':
            return ok(backtest);
          case '/backtests/{experiment_id}/trades':
            return ok([trade()]);
          case '/stats/occurrence':
            return ok({
              occurrence: {
                group: 'double_bottom',
                direction: 'bullish',
                quality: 71,
              },
              regime: 'uptrend/mid',
              outcomes: [],
            });
          case '/engine-runs/{run_id}/events':
            return ok([]);
          case '/forecast':
            return ok({
              empirical: {
                method: 'empirical',
                sample: 9,
                warnings: [],
                horizons: [],
              },
              knn: { method: 'knn', sample: 5, warnings: [], horizons: [] },
              thresholds: [0.5, 1, 2],
              warnings: [],
            });
          default:
            return ok([]);
        }
      }),
      POST: vi.fn(() => ok({})),
    };
    TestBed.configureTestingModule({
      providers: [
        provideTaiga(),
        provideEventPlugins(),
        provideRouter([]),
        { provide: API_CLIENT, useValue: client },
        { provide: JobsService, useValue: { watch: () => of() } },
      ],
    });
    const fixture = TestBed.createComponent(BacktestPage);
    const el = fixture.nativeElement as HTMLElement;
    fixture.detectChanges();
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('.history button')!.click();
    await settle(fixture);

    el.querySelector<HTMLElement>('tr.pick')!.click();
    await settle(fixture);

    const text = el.textContent ?? '';
    expect(text).toContain('Сделка лонг');
    expect(text).toContain('Режим на входе');
    expect(text).toContain('uptrend/mid');
    expect(text).toContain('активных уровней на момент входа нет');
    const link = el.querySelector<HTMLAnchorElement>('.drill a');
    expect(link?.getAttribute('href')).toBe(
      '/replay?root=3&tf=15m&date=2020-10-06',
    );
    expect(text).not.toContain('Прогноз построен');

    Array.from(el.querySelectorAll<HTMLButtonElement>('.drill button'))
      .find((b) => b.textContent?.includes('Прогноз на момент входа'))!
      .click();
    await settle(fixture);

    expect(el.textContent).toContain('Прогноз построен только по данным');
    expect(el.textContent).toContain('KNN');
  });
});
