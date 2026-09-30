import { TestBed } from '@angular/core/testing';
import type { Candle } from '@trader/api-client';
import { API_CLIENT } from '../../core/api/api';
import { ChartStore } from './chart.store';

const ok = <T>(data: T) => Promise.resolve({ data, response: { status: 200 } });

function candle(timestamp: string): Candle {
  return {
    timestamp,
    close_time: timestamp,
    open: '1',
    high: '2',
    low: '1',
    close: '2',
    volume: '3',
    is_partial: false,
  };
}

interface Query {
  root_id?: number;
  contract_id?: number;
  timeframe: string;
  end?: string;
  tail?: boolean;
  limit?: number;
}

function setup(pages: Record<string, unknown>[]) {
  const candlePages = [...pages];
  const client = {
    GET: vi.fn((path: string) => {
      if (path === '/roots') {
        return ok([{ id: 1, code: 'NG' }]);
      }
      if (path === '/roots/{root_id}/contracts') {
        return ok([{ id: 10, secid: 'NGZ6', expiration_date: '2026-12-29' }]);
      }
      return ok(candlePages.shift());
    }),
  };
  TestBed.configureTestingModule({
    providers: [ChartStore, { provide: API_CLIENT, useValue: client }],
  });
  const queries = () =>
    (
      client.GET.mock.calls as unknown as [
        string,
        { params: { query: Query } }?,
      ][]
    )
      .filter(([path]) => path === '/candles')
      .map(([, options]) => options?.params.query);
  return { store: TestBed.inject(ChartStore), queries };
}

describe('ChartStore', () => {
  it('загружает последние бары continuous выбранного root', async () => {
    const { store, queries } = setup([
      {
        candles: [candle('2026-09-28T04:00:00Z')],
        rolls: [],
        truncated: true,
      },
    ]);

    await store.loadRoots();

    expect(queries()).toEqual([
      { root_id: 1, timeframe: '1d', limit: 1500, tail: true },
    ]);
    expect(store.candles()).toHaveLength(1);
    expect(store.hasOlder()).toBe(true);
    expect(store.labels()).toEqual({ 10: 'NGZ6' });
    expect(store.loading()).toBe(false);
  });

  it('выбор контракта и таймфрейма перезагружает данные', async () => {
    const empty = { candles: [], rolls: [], truncated: false };
    const { store, queries } = setup([empty, empty, empty]);
    await store.loadRoots();

    await store.selectTarget({ kind: 'contract', id: 10 });
    await store.selectTimeframe('4h');

    expect(queries().slice(1)).toEqual([
      { contract_id: 10, timeframe: '1d', limit: 1500, tail: true },
      { contract_id: 10, timeframe: '4h', limit: 1500, tail: true },
    ]);
  });

  it('подгрузка истории просит бары левее первого и добавляет их слева', async () => {
    const { store, queries } = setup([
      {
        candles: [candle('2026-09-29T04:00:00Z')],
        rolls: [],
        truncated: true,
      },
      {
        candles: [candle('2026-09-28T04:00:00Z')],
        rolls: [],
        truncated: false,
      },
    ]);
    await store.loadRoots();

    await store.loadOlder();
    await store.loadOlder(); // старше уже нет — запроса не будет

    expect(queries()).toHaveLength(2);
    expect(queries()[1]?.end).toBe('2026-09-29T04:00:00Z');
    expect(store.candles().map((c) => c.timestamp)).toEqual([
      '2026-09-28T04:00:00Z',
      '2026-09-29T04:00:00Z',
    ]);
    expect(store.hasOlder()).toBe(false);
  });

  it('ответ на устаревший запрос не затирает свежий', async () => {
    const { store } = setup([]);
    const client = TestBed.inject(API_CLIENT) as unknown as {
      GET: ReturnType<typeof vi.fn>;
    };
    let releaseStale: (value: unknown) => void = () => undefined;
    const stale = new Promise((resolve) => (releaseStale = resolve));
    const fresh = {
      candles: [candle('2026-09-30T04:00:00Z')],
      rolls: [],
      truncated: false,
    };
    let calls = 0;
    client.GET.mockImplementation(() => (++calls === 1 ? stale : ok(fresh)));
    store.rootId.set(1);

    const first = store.selectTimeframe('1h'); // ответ придёт последним
    await store.selectTimeframe('4h');
    releaseStale({
      response: { status: 200 },
      data: {
        candles: [candle('2020-01-01T00:00:00Z')],
        rolls: [],
        truncated: false,
      },
    });
    await first;

    expect(store.candles().map((c) => c.timestamp)).toEqual([
      '2026-09-30T04:00:00Z',
    ]);
    expect(store.loading()).toBe(false);
  });
});
