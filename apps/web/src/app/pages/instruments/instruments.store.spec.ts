import { TestBed } from '@angular/core/testing';
import type { Job } from '@trader/api-client';
import { Observable, of, throwError } from 'rxjs';
import { API_CLIENT } from '../../core/api/api';
import { JobsService } from '../../core/jobs/jobs';
import { InstrumentsStore } from './instruments.store';

const ROOT = {
  id: 1,
  code: 'NG',
  name: 'Gas',
  exchange: 'MOEX',
  quote_currency: 'USD',
  tick_size: '0.00100000',
  calendar_code: 'moex_forts',
  roll_trading_days: 5,
  include_weekend_sessions: false,
  created_at: '2026-09-30T00:00:00Z',
};

const CONTRACT = {
  id: 10,
  root_id: 1,
  expiration_date: '2026-12-29',
  last_trade_date: null,
  secid: 'NGZ6',
  provider_ids: [],
};

const ok = <T>(data: T) => Promise.resolve({ data, response: { status: 200 } });

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: 7,
    type: 'iss.sync_root',
    params: {},
    status: 'succeeded',
    progress: 1,
    progress_message: null,
    result: null,
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

function setup(watch: (id: number) => Observable<Job> = () => of(job())) {
  const client = {
    GET: vi.fn((path: string) => {
      if (path === '/roots') {
        return ok([ROOT]);
      }
      if (path === '/roots/{root_id}/contracts') {
        return ok([CONTRACT]);
      }
      if (path === '/calendars/{code}') {
        return ok({ code: 'moex_forts', name: 'FORTS', rule_list: [] });
      }
      return ok({ holidays: ['2026-01-01'], special_days: [] });
    }),
    POST: vi.fn(),
    PATCH: vi.fn(() => ok(ROOT)),
    DELETE: vi.fn(() => Promise.resolve({ response: { status: 204 } })),
  };
  TestBed.configureTestingModule({
    providers: [
      InstrumentsStore,
      { provide: API_CLIENT, useValue: client },
      { provide: JobsService, useValue: { watch: vi.fn(watch) } },
    ],
  });
  return { client, store: TestBed.inject(InstrumentsStore) };
}

describe('InstrumentsStore', () => {
  it('загружает root, выбирает первый и подтягивает контракты и календарь', async () => {
    const { store } = setup();

    await store.loadRoots();

    expect(store.selected()?.code).toBe('NG');
    expect(store.contracts()).toHaveLength(1);
    expect(store.calendar()?.name).toBe('FORTS');
    expect(store.calendarDays()?.holidays).toEqual(['2026-01-01']);
  });

  it('создание root перезагружает список и выбирает новый', async () => {
    const { client, store } = setup();
    client.POST.mockImplementation(() => ok({ ...ROOT, id: 2, code: 'BR' }));
    await store.loadRoots();

    const created = await store.createRoot({
      code: 'BR',
      name: 'Brent',
      quote_currency: 'USD',
      tick_size: '0.01',
    });

    expect(created.code).toBe('BR');
    expect(client.POST).toHaveBeenCalledWith('/roots', expect.anything());
    expect(store.selectedId()).toBe(2);
  });

  it('удаление root снимает выбор', async () => {
    const { client, store } = setup();
    await store.loadRoots();
    client.GET.mockImplementation((path: string) =>
      path === '/roots' ? ok([]) : ok([]),
    );

    await store.deleteRoot(1);

    expect(client.DELETE).toHaveBeenCalled();
    expect(store.selected()).toBeNull();
    expect(store.contracts()).toEqual([]);
  });

  describe('контракты ISS', () => {
    const found = [
      { secid: 'NGZ6', expiration_date: '2026-12-29', last_trade_date: null },
      {
        secid: 'NGF7',
        expiration_date: '2027-01-27',
        last_trade_date: '2027-01-27',
      },
    ];

    it('отмечает существующие и предвыбирает только новые', async () => {
      const { client, store } = setup(() =>
        of(job({ result: { contracts: found } })),
      );
      client.POST.mockImplementation(() => ok(job({ status: 'queued' })));
      await store.loadRoots();

      await store.requestIssContracts(2026);

      const [old, fresh] = store.issCandidates();
      expect(old).toMatchObject({
        secid: 'NGZ6',
        exists: true,
        selected: false,
      });
      expect(fresh).toMatchObject({
        secid: 'NGF7',
        exists: false,
        selected: true,
      });
      expect(store.issSelectedCount()).toBe(1);
      expect(store.issError()).toBeNull();
    });

    it('подтверждение отправляет только отмеченные и обновляет контракты', async () => {
      const { client, store } = setup(() =>
        of(job({ result: { contracts: found } })),
      );
      client.POST.mockImplementation((path: string) =>
        path.endsWith('iss-preview')
          ? ok(job({ status: 'queued' }))
          : ok({ contracts: [], created: 1, imports_enqueued: [5] }),
      );
      await store.loadRoots();
      await store.requestIssContracts(2026);

      const created = await store.confirmIssContracts(true);

      expect(created).toBe(1);
      const [, request] = client.POST.mock.calls.at(-1) as unknown as [
        string,
        { body: { contracts: { secid: string }[]; enqueue_imports: boolean } },
      ];
      expect(request.body.contracts.map((c) => c.secid)).toEqual(['NGF7']);
      expect(request.body.enqueue_imports).toBe(true);
      expect(store.issCandidates()).toEqual([]);
    });

    it('неуспешная задача показывает ошибку и не даёт кандидатов', async () => {
      const { client, store } = setup(() =>
        of(job({ status: 'failed', error: 'ISS недоступен: 503' })),
      );
      client.POST.mockImplementation(() => ok(job({ status: 'queued' })));
      await store.loadRoots();

      await store.requestIssContracts(2026);

      expect(store.issError()).toBe('ISS недоступен: 503');
      expect(store.issCandidates()).toEqual([]);
    });

    it('обрыв соединения с WebSocket превращается в сообщение', async () => {
      const { client, store } = setup(() =>
        throwError(() => new Error('Соединение с сервером прервано')),
      );
      client.POST.mockImplementation(() => ok(job({ status: 'queued' })));
      await store.loadRoots();

      await store.requestIssContracts(2026);

      expect(store.issError()).toContain('прервано');
    });

    it('выбор можно менять по одному и целиком', async () => {
      const { client, store } = setup(() =>
        of(job({ result: { contracts: found } })),
      );
      client.POST.mockImplementation(() => ok(job({ status: 'queued' })));
      await store.loadRoots();
      await store.requestIssContracts(2026);

      store.setAllCandidates(true);
      expect(store.issSelectedCount()).toBe(2);
      store.toggleCandidate('NGZ6', false);
      expect(store.issSelectedCount()).toBe(1);
    });
  });
});
