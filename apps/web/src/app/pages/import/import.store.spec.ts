import { TestBed } from '@angular/core/testing';
import type { FileMapping, Job } from '@trader/api-client';
import { Observable, of } from 'rxjs';
import { API_CLIENT } from '../../core/api/api';
import { JobsService } from '../../core/jobs/jobs';
import { ImportStore } from './import.store';
import { fromMapping } from './mapping';

const ok = <T>(data: T) => Promise.resolve({ data, response: { status: 200 } });

const ROOT = { id: 1, code: 'NG', name: 'Gas' };
const CONTRACT = {
  id: 10,
  root_id: 1,
  expiration_date: '2026-12-29',
  secid: 'NGZ6',
};
const FINAM: FileMapping = {
  delimiter: ',',
  has_header: true,
  encoding: 'utf-8-sig',
  skip_rows: 0,
  decimal_separator: '.',
  timezone: 'Europe/Moscow',
  timestamp_is_close: false,
  datetime: { columns: ['<DATE>', '<TIME>'], format: '%Y%m%d %H%M%S' },
  open: '<OPEN>',
  high: '<HIGH>',
  low: '<LOW>',
  close: '<CLOSE>',
  volume: '<VOL>',
  trade_count: null,
};
const RECORD = {
  id: 55,
  contract_id: 10,
  provider: 'csv',
  kind: 'import',
  status: 'completed',
  report: { inserted: 3, conflicts: 1 },
};

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: 7,
    type: 'import.file',
    params: {},
    status: 'succeeded',
    progress: 1,
    progress_message: null,
    result: { import_id: 55 },
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
      switch (path) {
        case '/roots':
          return ok([ROOT]);
        case '/roots/{root_id}/contracts':
          return ok([CONTRACT]);
        case '/imports':
          return ok([RECORD]);
        case '/imports/{import_id}':
          return ok(RECORD);
        case '/imports/{import_id}/errors':
          return ok([
            { row_number: 4, reason_code: 'bad_number', message: 'x' },
          ]);
        case '/imports/{import_id}/conflicts':
          return ok([{ id: 1 }, { id: 2 }]);
        case '/import-files':
          return ok([
            { name: 'a.csv', size: 10, modified_at: '2026-09-30T00:00:00Z' },
          ]);
        case '/import-presets':
          return ok([
            { name: 'finam', builtin: true, description: null, mapping: FINAM },
          ]);
        default:
          return ok({
            name: 'a.csv',
            size: 10,
            encoding: 'utf-8-sig',
            lines: ['a;b'],
            delimiter: ';',
          });
      }
    }),
    POST: vi.fn(() => ok(job({ status: 'queued' }))),
    PUT: vi.fn(() => ok({})),
  };
  TestBed.configureTestingModule({
    providers: [
      ImportStore,
      { provide: API_CLIENT, useValue: client },
      { provide: JobsService, useValue: { watch: vi.fn(watch) } },
    ],
  });
  return { client, store: TestBed.inject(ImportStore) };
}

describe('ImportStore', () => {
  it('выбирает первый root и последний контракт, грузит историю', async () => {
    const { store } = setup();

    await store.loadRoots();

    expect(store.rootId()).toBe(1);
    expect(store.contractId()).toBe(10);
    expect(store.history()).toHaveLength(1);
  });

  it('запуск ISS передаёт окно дат и без import_id не открывает отчёт', async () => {
    const { client, store } = setup(() =>
      of(job({ type: 'import.iss', result: { chunks: 2, inserted: 5 } })),
    );
    await store.loadRoots();

    await store.startIss('2026-01-01', '');

    const [path, request] = client.POST.mock.calls[0] as unknown as [
      string,
      { body: { from: string | null; till: string | null } },
    ];
    expect(path).toBe('/contracts/{contract_id}/imports/iss');
    expect(request.body).toEqual({ from: '2026-01-01', till: null });
    expect(store.progress()?.job.status).toBe('succeeded');
    expect(store.report()).toBeNull();
  });

  it('файл: пресет заполняет форму, запуск отправляет маппинг и открывает отчёт', async () => {
    const { client, store } = setup();
    await store.loadRoots();
    await store.loadFileSetup();
    await store.selectFile('a.csv');

    store.applyPreset('finam');
    await store.startFile();

    expect(store.form()).toEqual(fromMapping(FINAM));
    const [, request] = client.POST.mock.calls[0] as unknown as [
      string,
      { body: { file: string; mapping: unknown } },
    ];
    expect(request.body.file).toBe('a.csv');
    expect(request.body.mapping).toEqual(FINAM);
    expect(store.report()?.id).toBe(55);
    expect(store.rejected()).toHaveLength(1);
    expect(store.conflicts()).toHaveLength(2);
  });

  it('разделитель подсказывается по предпросмотру', async () => {
    const { store } = setup();
    await store.selectFile('a.csv');

    expect(store.preview()?.delimiter).toBe(';');
    expect(store.form().delimiter).toBe(';');
  });

  it('незаполненный маппинг не отправляется', async () => {
    const { client, store } = setup();
    await store.loadRoots();
    await store.selectFile('a.csv');

    await expect(store.startFile()).rejects.toThrow('Укажите колонку');
    expect(client.POST).not.toHaveBeenCalled();
  });

  it('обрыв WebSocket сохраняет последнее состояние и сообщение', async () => {
    const { store } = setup(
      () =>
        new Observable<Job>((subscriber) => {
          subscriber.next(job({ status: 'running', progress: 0.4 }));
          subscriber.error(new Error('Соединение с сервером прервано'));
        }),
    );
    await store.loadRoots();

    await store.startIss(null, null);

    expect(store.progress()?.job.progress).toBe(0.4);
    expect(store.progress()?.watchError).toContain('прервано');
    expect(store.running()).toBe(true);
  });

  it('разрешение конфликтов: выбранные или все ожидающие', async () => {
    const { client, store } = setup();
    await store.loadRoots();
    await store.openReport(55);

    await store.resolveConflicts(true);
    store.toggleConflict(2, true);
    await store.resolveConflicts(false);

    const bodies = client.POST.mock.calls.map(
      (call) => (call as unknown as [string, { body: unknown }])[1].body,
    );
    expect(bodies).toEqual([
      { accept: true, conflict_ids: null },
      { accept: false, conflict_ids: [2] },
    ]);
    expect(store.chosenConflicts().size).toBe(0);
  });
});
