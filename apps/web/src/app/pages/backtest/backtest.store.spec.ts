import { TestBed } from '@angular/core/testing';
import type { Backtest, BacktestLock, Job } from '@trader/api-client';
import { Observable, of, throwError } from 'rxjs';
import { API_CLIENT } from '../../core/api/api';
import { JobsService } from '../../core/jobs/jobs';
import { BacktestStore } from './backtest.store';

const ok = (data: unknown) =>
  Promise.resolve({ data, response: { status: 200 } });

const created = (overrides: Partial<Backtest> = {}): Backtest =>
  ({
    id: 7,
    kind: 'single',
    status: 'queued',
    job_id: 70,
    timeframe_code: '1h',
    family: 'pattern',
    params_hash: 'abc',
    result: null,
    error: null,
    ...overrides,
  }) as Backtest;

const finished = (kind: 'single' | 'walk_forward' = 'single') =>
  created({ kind, status: 'succeeded', result: { metrics: {} } });

interface Request {
  body?: Record<string, unknown>;
  params?: {
    path?: Record<string, unknown>;
    query?: Record<string, unknown>;
  };
}
type Call = [string, Request?];
const calls = (mock: ReturnType<typeof vi.fn>) =>
  mock.mock.calls as unknown as Call[];

function setup(watch?: Observable<Job>) {
  const client = {
    GET: vi.fn((path: string) => {
      switch (path) {
        case '/roots':
          return ok([{ id: 1, code: 'NG' }]);
        case '/backtests':
          return ok([finished()]);
        case '/backtest-locks':
          return ok([]);
        case '/backtests/{experiment_id}':
          return ok(finished());
        case '/backtests/{experiment_id}/trades':
          return ok([{ id: 1 }]);
        case '/backtests/{experiment_id}/windows':
          return ok([{ id: 2 }]);
        default:
          return ok([]);
      }
    }),
    POST: vi.fn((path: string) =>
      path === '/backtests' ? ok(created()) : ok({ unlocked: true }),
    ),
  };
  const jobs = {
    watch: vi.fn(
      () =>
        watch ??
        of({ progress: 0.5, progress_message: 'прогон' } as unknown as Job),
    ),
  };
  TestBed.configureTestingModule({
    providers: [
      BacktestStore,
      { provide: API_CLIENT, useValue: client },
      { provide: JobsService, useValue: jobs },
    ],
  });
  return { client, jobs, store: TestBed.inject(BacktestStore) };
}

const ready = (store: BacktestStore) =>
  store.patch({ rootId: 1, periodFrom: '2026-01-01', periodTo: '2026-06-30' });

describe('BacktestStore', () => {
  it('init: инструменты, первый выбирается, история и блокировки', async () => {
    const { store } = setup();

    await store.init();

    expect(store.roots()).toHaveLength(1);
    expect(store.form().rootId).toBe(1);
    expect(store.history()).toHaveLength(1);
    expect(store.locks()).toEqual([]);
  });

  it('run: запрос, ожидание задачи и загрузка результата', async () => {
    const { store, client, jobs } = setup();
    ready(store);

    await store.run();

    const [path, request] = calls(client.POST)[0] ?? [];
    expect(path).toBe('/backtests');
    expect(request?.body).toMatchObject({
      root_id: 1,
      period_from: '2026-01-01',
    });
    expect(jobs.watch).toHaveBeenCalledWith(70);
    expect(store.current()?.status).toBe('succeeded');
    expect(store.trades()).toEqual([{ id: 1 }]);
    expect(store.windows()).toEqual([]); // single — без окон
    expect(store.running()).toBe(false);
    expect(store.progress()).toBeNull();
    expect(store.error()).toBeNull();
    expect(calls(client.GET).some(([p]) => p === '/backtests')).toBe(true);
  });

  it('walk-forward подгружает окна', async () => {
    const { store, client } = setup();
    client.GET.mockImplementation((path: string) =>
      path === '/backtests/{experiment_id}'
        ? ok(finished('walk_forward'))
        : path === '/backtests/{experiment_id}/windows'
          ? ok([{ id: 2 }])
          : ok([]),
    );

    await store.open(7);

    expect(store.windows()).toEqual([{ id: 2 }]);
  });

  it('ошибка формы не отправляет запрос', async () => {
    const { store, client } = setup();

    await store.run();

    expect(client.POST).not.toHaveBeenCalled();
    expect(store.error()).toContain('инструмент');
    expect(store.running()).toBe(false);
  });

  it('ошибка задачи и обрыв связи показываются, запуск освобождается', async () => {
    const { store, client } = setup(
      throwError(() => new Error('Нет связи с сервером')),
    );
    // задача ещё не завершилась: сообщение об обрыве не затирается
    client.GET.mockImplementation((path: string) =>
      path === '/backtests/{experiment_id}' ? ok(created()) : ok([]),
    );
    ready(store);

    await store.run();

    expect(store.error()).toBe('Нет связи с сервером');
    expect(store.running()).toBe(false);
  });

  it('упавший бэктест показывает причину и не грузит сделки', async () => {
    const { store, client } = setup();
    client.GET.mockImplementation((path: string) =>
      path === '/backtests/{experiment_id}'
        ? ok(created({ status: 'failed', error: 'Неверная стратегия: x' }))
        : ok([]),
    );

    await store.open(7);

    expect(store.error()).toBe('Неверная стратегия: x');
    expect(
      calls(client.GET).some(
        ([p]) => p === '/backtests/{experiment_id}/trades',
      ),
    ).toBe(false);
  });

  it('ответ API с ошибкой попадает в error', async () => {
    const { store, client } = setup();
    client.POST.mockImplementation(
      () =>
        Promise.resolve({
          error: { detail: 'Test-период должен идти после' },
          response: { status: 422 },
        }) as never,
    );
    ready(store);

    await store.run();

    expect(store.error()).toBeTruthy();
    expect(store.running()).toBe(false);
  });

  it('unlock: причина уходит в API, блокировки перечитываются', async () => {
    const { store, client } = setup();
    const lock = {
      id: 1,
      root_id: 1,
      timeframe_code: '1h',
      family: 'pattern',
    } as BacktestLock;

    const done = await store.unlock(lock, 'ошибка в данных');

    expect(done).toBe(true);
    const [path, request] = calls(client.POST).at(-1) ?? [];
    expect(path).toBe('/backtest-locks/unlock');
    expect(request?.body).toEqual({
      root_id: 1,
      timeframe: '1h',
      family: 'pattern',
      note: 'ошибка в данных',
    });
    expect(
      calls(client.GET).filter(([p]) => p === '/backtest-locks'),
    ).toHaveLength(1);
  });
});
