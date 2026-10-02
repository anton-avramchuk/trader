import { TestBed } from '@angular/core/testing';
import type { Job } from '@trader/api-client';
import { of, throwError } from 'rxjs';
import { API_CLIENT } from '../../core/api/api';
import { JobsService } from '../../core/jobs/jobs';
import { InstrumentsStore } from './instruments.store';

const ok = <T>(data: T) => Promise.resolve({ data, response: { status: 200 } });
const fail = (status: number, detail: string) =>
  Promise.resolve({ error: { detail }, response: { status } });

const SBER = { id: 1, ticker: 'SBER', coverage: [] };
const GAZP = { id: 2, ticker: 'GAZP', coverage: [] };
const job = (status: string, extra: Partial<Job> = {}) =>
  ({ id: 7, type: 'candles.load', status, ...extra }) as Job;

function setup(watch = of(job('succeeded'))) {
  const client = {
    GET: vi.fn((path: string) => {
      switch (path) {
        case '/instruments':
          return ok([GAZP, SBER]);
        case '/instruments/{instrument_id}/loads':
          return ok([{ id: 1, timeframe_code: '1h', rows: 5 }]);
        case '/importer/tickers':
          return ok([
            { ticker: 'SBER', added: true },
            { ticker: 'LKOH', added: false },
          ]);
        default:
          return ok(undefined);
      }
    }),
    POST: vi.fn((path: string) =>
      path === '/instruments'
        ? ok(SBER)
        : ok(job('queued', { params: { instrument_id: 1 } })),
    ),
    PATCH: vi.fn(() => ok(SBER)),
  };
  TestBed.configureTestingModule({
    providers: [
      InstrumentsStore,
      { provide: API_CLIENT, useValue: client },
      { provide: JobsService, useValue: { watch: () => watch } },
    ],
  });
  return { client, store: TestBed.inject(InstrumentsStore) };
}

describe('InstrumentsStore', () => {
  it('загружает инструменты и выбирает первый с журналом загрузок', async () => {
    const { store } = setup();

    await store.load();

    expect(store.instruments().map((i) => i.ticker)).toEqual(['GAZP', 'SBER']);
    expect(store.selected()?.ticker).toBe('GAZP');
    expect(store.loads()).toHaveLength(1);
  });

  it('тикеры importer: добавленные не предлагаются', async () => {
    const { store } = setup();

    await store.loadTickers();

    expect(store.available().map((t) => t.ticker)).toEqual(['LKOH']);
    expect(store.tickersError()).toBeNull();
  });

  it('недоступный importer — сообщение, а не исключение', async () => {
    const { client, store } = setup();
    client.GET.mockImplementation(
      () => fail(502, 'Importer недоступен') as never,
    );

    await store.loadTickers();

    expect(store.tickers()).toEqual([]);
    expect(store.tickersError()).toContain('Importer');
  });

  it('добавление создаёт инструмент и выбирает его', async () => {
    const { client, store } = setup();

    await store.add('SBER', 2.5);

    expect(client.POST).toHaveBeenCalledWith('/instruments', {
      body: { ticker: 'SBER', tick_value: 2.5 },
    });
    expect(store.selectedId()).toBe(1);
  });

  it('стоимость тика меняется PATCH-ом', async () => {
    const { client, store } = setup();

    await store.setTickValue(1, null);

    expect(client.PATCH).toHaveBeenCalledWith('/instruments/{instrument_id}', {
      params: { path: { instrument_id: 1 } },
      body: { tick_value: null },
    });
  });

  it('загрузка свечей ставит задачу, ждёт конца и обновляет покрытие', async () => {
    const { client, store } = setup();
    await store.load();

    await store.loadCandles(1, { from: '2026-01-01', to: '2026-02-01' }, [
      '1h',
      '1d',
    ]);

    expect(client.POST).toHaveBeenCalledWith(
      '/instruments/{instrument_id}/load',
      {
        params: { path: { instrument_id: 1 } },
        body: {
          period_from: '2026-01-01',
          period_to: '2026-02-01',
          timeframes: ['1h', '1d'],
        },
      },
    );
    expect(store.loadJob()?.status).toBe('succeeded');
    expect(store.loadRunning()).toBe(false);
    expect(client.GET).toHaveBeenCalledWith('/instruments');
  });

  it('обрыв связи с задачей помечает загрузку как неуспешную', async () => {
    const { store } = setup(throwError(() => new Error('Нет связи')) as never);

    await store.select(1);

    await store.loadCandles(1, { from: '2026-01-01', to: '2026-02-01' }, null);

    expect(store.loadJob()).toMatchObject({
      status: 'failed',
      error: 'Нет связи',
    });
  });

  it('load выбирает инструмент из адреса, а неизвестный тикер заменяет первым', async () => {
    const first = setup().store;
    await first.load('SBER');
    expect(first.selectedId()).toBe(1);

    TestBed.resetTestingModule();
    const second = setup().store;
    await second.load('NOPE');
    expect(second.selectedId()).toBe(2); // первый в списке — GAZP
  });

  it('повторный load не сбрасывает уже выбранный инструмент', async () => {
    const { store } = setup();
    await store.load('SBER');

    await store.load('GAZP');

    expect(store.selectedId()).toBe(1);
  });
});
