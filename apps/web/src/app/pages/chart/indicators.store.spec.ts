import { TestBed } from '@angular/core/testing';
import type { IndicatorInfo, IndicatorValues, Job } from '@trader/api-client';
import { Observable, of, throwError } from 'rxjs';
import { API_CLIENT } from '../../core/api/api';
import { JobsService } from '../../core/jobs/jobs';
import { IndicatorsStore } from './indicators.store';

const ok = <T>(data: T) => Promise.resolve({ data, response: { status: 200 } });

const EMA: IndicatorInfo = {
  name: 'ema',
  title: 'EMA — экспоненциальная скользящая средняя',
  version: 1,
  pane: 'price',
  outputs: ['value'],
  warmup_bars: 20,
  params_schema: { properties: { period: { type: 'integer' } } },
  defaults: { period: 20 },
};

function valuesFor(query: {
  indicator: string;
  source_timeframe: string;
}): IndicatorValues {
  return {
    indicator: query.indicator,
    version: 1,
    params: {},
    params_hash: 'h',
    pane: 'price',
    outputs: ['value'],
    chart_timeframe: '15m',
    source_timeframe: query.source_timeframe,
    warmup_bars: 20,
    source_bar_count: 100,
    source_truncated: false,
    as_of: null,
    points: [
      {
        timestamp: '2026-09-28T04:00:00Z',
        values: { value: 1 },
        valid: true,
        source_timestamp: null,
        available_at: null,
      },
    ],
  };
}

interface Query {
  indicator: string;
  chart_timeframe: string;
  source_timeframe: string;
  params: string;
  as_of?: string;
  start?: string;
  root_id?: number;
}

function setup(failFor?: string) {
  const client = {
    GET: vi.fn((path: string, request?: { params: { query: Query } }) => {
      if (path === '/indicators') {
        return ok([EMA]);
      }
      const query = request?.params.query as Query;
      if (failFor && query.indicator === failFor) {
        return Promise.resolve({
          error: { detail: 'Некорректные параметры' },
          response: { status: 422 },
        });
      }
      return ok(valuesFor(query));
    }),
  };
  TestBed.configureTestingModule({
    providers: [IndicatorsStore, { provide: API_CLIENT, useValue: client }],
  });
  const queries = () =>
    (
      client.GET.mock.calls as unknown as [
        string,
        { params: { query: Query } }?,
      ][]
    )
      .filter(([path]) => path === '/indicator-values')
      .map(([, request]) => request?.params.query as Query);
  return { client, store: TestBed.inject(IndicatorsStore), queries };
}

describe('IndicatorsStore', () => {
  it('загружает каталог', async () => {
    const { store } = setup();

    await store.loadCatalog();

    expect(store.catalog().map((i) => i.name)).toEqual(['ema']);
  });

  it('добавление до появления контекста только запоминает индикатор', async () => {
    const { store, queries } = setup();

    await store.add(EMA, { period: 50 }, '1h');

    expect(store.active()).toHaveLength(1);
    expect(queries()).toEqual([]);
  });

  it('refresh считает все активные для контекста и строит серии', async () => {
    const { store, queries } = setup();
    await store.add(EMA, { period: 50 }, '1h');

    await store.refresh({
      root_id: 1,
      chartTimeframe: '15m',
      start: '2026-09-28T00:00:00Z',
    });

    expect(queries()).toEqual([
      expect.objectContaining({
        indicator: 'ema',
        chart_timeframe: '15m',
        source_timeframe: '1h',
        params: '{"period":50}',
        root_id: 1,
        start: '2026-09-28T00:00:00Z',
      }),
    ]);
    expect(store.series()).toHaveLength(1);
    expect(store.series()[0].title).toBe('EMA(50) · 1h');
  });

  it('добавление после контекста считает сразу', async () => {
    const { store, queries } = setup();
    await store.refresh({ contract_id: 5, chartTimeframe: '1h' });

    await store.add(EMA, {}, '1h');

    expect(queries()).toHaveLength(1);
    expect(store.values()[store.active()[0].id]).toBeDefined();
  });

  it('as_of передаётся серверу (replay)', async () => {
    const { store, queries } = setup();
    await store.add(EMA, {}, '15m');

    await store.refresh({
      root_id: 1,
      chartTimeframe: '15m',
      asOf: '2026-09-28T10:00:00Z',
    });

    expect(queries()[0].as_of).toBe('2026-09-28T10:00:00Z');
  });

  it('remove убирает индикатор, его значения и ошибку', async () => {
    const { store } = setup();
    await store.refresh({ root_id: 1, chartTimeframe: '15m' });
    await store.add(EMA, {}, '15m');
    const id = store.active()[0].id;

    store.remove(id);

    expect(store.active()).toEqual([]);
    expect(store.values()).toEqual({});
    expect(store.series()).toEqual([]);
  });

  it('ошибка одного индикатора не мешает остальным и показывается по его id', async () => {
    const { store } = setup('bad');
    await store.refresh({ root_id: 1, chartTimeframe: '15m' });
    await store.add(EMA, {}, '15m');
    await store.add({ ...EMA, name: 'bad' }, {}, '15m');

    const [good, bad] = store.active();

    expect(store.values()[good.id]).toBeDefined();
    expect(store.errors()[bad.id]).toContain('Некорректные параметры');
  });

  it('после смены chart TF source TF, ставший младше, поднимается до chart TF', async () => {
    const { store, queries } = setup();
    await store.add(EMA, {}, '15m');

    await store.refresh({ root_id: 1, chartTimeframe: '4h' });

    expect(store.active()[0].sourceTimeframe).toBe('4h');
    expect(queries().at(-1)?.source_timeframe).toBe('4h');
  });

  it('replaceAll заменяет набор (загрузка профиля)', async () => {
    const { store } = setup();
    await store.refresh({ root_id: 1, chartTimeframe: '15m' });
    await store.add(EMA, {}, '15m');

    await store.replaceAll(
      [
        {
          name: 'ema',
          title: 'EMA',
          params: { period: 200 },
          sourceTimeframe: '1h',
        },
      ],
      { ema: EMA.title },
    );

    expect(store.active()).toHaveLength(1);
    expect(store.active()[0]).toMatchObject({
      params: { period: 200 },
      title: EMA.title,
    });
  });
});

describe('IndicatorsStore.verify', () => {
  const REPORT = {
    timeframe: '15m',
    bars: 100,
    range: ['2026-09-28T04:00:00Z', '2026-09-29T04:00:00Z'],
    ok: true,
    reports: [
      {
        indicator: 'ema',
        params: { period: 20 },
        bars: 100,
        positions_checked: 100,
        values_checked: 100,
        mismatch_count: 0,
        ok: true,
        mismatches: [],
      },
    ],
  };

  function job(overrides: Partial<Job> = {}): Job {
    return {
      id: 5,
      type: 'verify.indicators',
      params: {},
      status: 'succeeded',
      progress: 1,
      progress_message: null,
      result: REPORT as unknown as Job['result'],
      error: null,
      attempts: 1,
      max_attempts: 3,
      cancel_requested: false,
      created_at: '2026-09-30T00:00:00Z',
      started_at: null,
      finished_at: null,
      ...overrides,
    };
  }

  function setupVerify(watch: () => Observable<Job>) {
    const client = {
      GET: vi.fn(() => ok({})),
      POST: vi.fn(() => ok(job({ status: 'queued', result: null }))),
    };
    TestBed.configureTestingModule({
      providers: [
        IndicatorsStore,
        { provide: API_CLIENT, useValue: client },
        { provide: JobsService, useValue: { watch: vi.fn(watch) } },
      ],
    });
    return { client, store: TestBed.inject(IndicatorsStore) };
  }

  it('без контекста графика ничего не запускает', async () => {
    const { store, client } = setupVerify(() => of(job()));

    await store.verify();

    expect(client.POST).not.toHaveBeenCalled();
  });

  it('ставит задачу для диапазона графика с активными индикаторами и разбирает отчёт', async () => {
    const { store, client } = setupVerify(() => of(job()));
    await store.add(EMA, { period: 20 }, '15m');
    store.refresh({
      root_id: 3,
      chartTimeframe: '15m',
      start: '2026-09-28T04:00:00Z',
    });

    await store.verify();

    const [path, request] = client.POST.mock.calls.at(-1) as unknown as [
      string,
      { body: { type: string; params: Record<string, unknown> } },
    ];
    expect(path).toBe('/jobs');
    expect(request.body.type).toBe('verify.indicators');
    expect(request.body.params).toMatchObject({
      root_id: 3,
      timeframe: '15m',
      start: '2026-09-28T04:00:00Z',
      indicators: [{ name: 'ema', params: { period: 20 } }],
    });
    expect(store.verifyResult()?.ok).toBe(true);
    expect(store.verifyError()).toBeNull();
  });

  it('без активных индикаторов проверяются все (список не передаётся); в replay конец — as_of', async () => {
    const { store, client } = setupVerify(() => of(job()));
    store.refresh({
      contract_id: 8,
      chartTimeframe: '1h',
      start: '2026-09-28T04:00:00Z',
      asOf: '2026-09-28T12:00:00Z',
    });

    await store.verify();

    const [, request] = client.POST.mock.calls.at(-1) as unknown as [
      string,
      { body: { params: Record<string, unknown> } },
    ];
    expect(request.body.params).not.toHaveProperty('indicators');
    expect(request.body.params).toMatchObject({
      contract_id: 8,
      end: '2026-09-28T12:00:00Z',
    });
  });

  it('упавшая задача показывает причину, обрыв связи — сообщение', async () => {
    const failed = setupVerify(() =>
      of(
        job({
          status: 'failed',
          error: 'В выбранном диапазоне нет баров',
          result: null,
        }),
      ),
    );
    failed.store.refresh({ root_id: 1, chartTimeframe: '15m' });
    await failed.store.verify();
    expect(failed.store.verifyError()).toBe('В выбранном диапазоне нет баров');
    expect(failed.store.verifyResult()).toBeNull();

    TestBed.resetTestingModule();
    const dropped = setupVerify(() =>
      throwError(() => new Error('Соединение с сервером прервано')),
    );
    dropped.store.refresh({ root_id: 1, chartTimeframe: '15m' });
    await dropped.store.verify();
    expect(dropped.store.verifyError()).toContain('прервано');
  });
});
