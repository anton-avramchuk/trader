import { TestBed } from '@angular/core/testing';
import type { Candle } from '@trader/api-client';
import { API_CLIENT } from '../../core/api/api';
import { FORWARD_PAGE, LOOKBACK, ReplayStore } from './replay.store';

const ok = <T>(data: T) => Promise.resolve({ data, response: { status: 200 } });

function bar(index: number): Candle {
  const start = Date.UTC(2026, 8, 28, 0) + index * 3600_000;
  return {
    timestamp: new Date(start).toISOString(),
    close_time: new Date(start + 3600_000).toISOString(),
    open: '100',
    high: '101',
    low: '99',
    close: String(100 + index),
    volume: '1',
    trading_day: '2026-09-28',
  };
}

const range = (from: number, to: number) =>
  Array.from({ length: to - from }, (_, i) => bar(from + i));

interface Query {
  start?: string;
  end?: string;
  tail?: boolean;
  limit?: number;
  as_of?: string;
}

function setup(options: { forward?: Candle[]; more?: boolean } = {}) {
  const past = range(-3, 0);
  const forward = options.forward ?? range(0, 6);
  const client = {
    GET: vi.fn((path: string, request?: { params: { query: Query } }) => {
      if (path === '/instruments') {
        return ok([{ id: 1, ticker: 'SBER' }]);
      }
      if (path === '/snapshot') {
        return ok({ candles: past.slice(-1), truncated: false });
      }
      const query = request?.params.query;
      if (query?.tail) {
        return ok({
          candles: past,
          truncated: false,
          next_start: null,
        });
      }
      return ok({
        candles: forward,
        truncated: options.more ?? false,
        next_start: options.more ? forward.at(-1)?.close_time : null,
      });
    }),
  };
  TestBed.configureTestingModule({
    providers: [ReplayStore, { provide: API_CLIENT, useValue: client }],
  });
  return { client, store: TestBed.inject(ReplayStore) };
}

async function started(options?: Parameters<typeof setup>[0]) {
  const ctx = setup(options);
  await ctx.store.loadInstruments();
  await ctx.store.start('2026-09-28');
  return ctx;
}

describe('ReplayStore', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('старт: история слева видна, будущее скрыто', async () => {
    const { store, client } = await started();

    expect(store.timeline()).toHaveLength(9);
    expect(store.cursor()).toBe(3);
    expect(store.visible()).toHaveLength(3);
    expect(store.asOf()).toBe(bar(-1).close_time);
    const queries = client.GET.mock.calls
      .filter(([path]) => path === '/candles')
      .map(([, request]) => request?.params.query);
    expect(queries[0]).toMatchObject({
      end: '2026-09-27T21:00:00.000Z',
      tail: true,
      limit: LOOKBACK,
    });
    expect(queries[1]).toMatchObject({
      start: '2026-09-27T21:00:00.000Z',
      limit: FORWARD_PAGE,
    });
  });

  it('Next / +10 двигают курсор вперёд, но не за конец данных', async () => {
    const { store } = await started();

    await store.move(1);
    expect(store.visible().at(-1)?.close).toBe('100');
    await store.move(10);

    expect(store.cursor()).toBe(9);
    expect(store.atEnd()).toBe(true);
  });

  it('перемотка назад уменьшает видимое, но не ниже нуля', async () => {
    const { store } = await started();
    await store.move(4);

    await store.move(-2);
    expect(store.cursor()).toBe(5);
    await store.move(-100);

    expect(store.cursor()).toBe(0);
    expect(store.visible()).toEqual([]);
    expect(store.asOf()).toBeNull();
  });

  it('Restart возвращает курсор в точку старта и ставит на паузу', async () => {
    const { store } = await started();
    await store.move(5);
    store.play();

    store.restart();

    expect(store.cursor()).toBe(3);
    expect(store.playing()).toBe(false);
  });

  it('Play двигает курсор со скоростью и останавливается в конце', async () => {
    vi.useFakeTimers();
    const { store } = await started();
    store.setSpeed(10);

    store.play();
    expect(store.playing()).toBe(true);
    await vi.advanceTimersByTimeAsync(300);
    expect(store.cursor()).toBe(6);
    await vi.advanceTimersByTimeAsync(2000);

    expect(store.cursor()).toBe(9);
    expect(store.playing()).toBe(false);
  });

  it('Pause останавливает движение', async () => {
    vi.useFakeTimers();
    const { store } = await started();
    store.setSpeed(10);
    store.play();
    await vi.advanceTimersByTimeAsync(200);

    store.pause();
    const frozen = store.cursor();
    await vi.advanceTimersByTimeAsync(1000);

    expect(store.cursor()).toBe(frozen);
  });

  it('дойдя до конца страницы, подгружает следующую', async () => {
    const { store, client } = await started({
      forward: range(0, 3),
      more: true,
    });
    const forwardCalls = () =>
      client.GET.mock.calls.filter(
        ([path, request]) => path === '/candles' && !request?.params.query.tail,
      );
    expect(forwardCalls()).toHaveLength(1);

    await store.move(6);

    expect(forwardCalls()).toHaveLength(2);
    expect(store.timeline().length).toBeGreaterThan(6);
    expect(store.cursor()).toBe(9);
  });

  it('Jump to date загружает новую точку старта', async () => {
    const { store, client } = await started();
    await store.move(5);

    await store.start('2026-10-05');

    expect(store.cursor()).toBe(3);
    const last = client.GET.mock.calls
      .filter(([p]) => p === '/candles')
      .at(-1)?.[1];
    expect(last?.params.query.start).toBe('2026-10-04T21:00:00.000Z');
  });

  it('сверка с сервером сравнивает показанные бары со snapshot', async () => {
    const { store, client } = await started();

    await store.verifyWithServer();

    expect(store.verify()).toMatchObject({ checked: 1, mismatches: 0 });
    const call = client.GET.mock.calls.find(([p]) => p === '/snapshot');
    expect(call?.[1]?.params.query.as_of).toBe(bar(-1).close_time);
  });

  it('смена таймфрейма сбрасывает состояние и останавливает воспроизведение', async () => {
    const { store } = await started();
    store.play();

    store.selectTimeframe('4h');

    expect(store.playing()).toBe(false);
    expect(store.timeline()).toEqual([]);
    expect(store.cursor()).toBe(0);
  });
});
