import { TestBed } from '@angular/core/testing';
import type { Job } from '@trader/api-client';
import { isFinished, jobSocketUrl, JobsService, SOCKET_FACTORY } from './jobs';

class FakeSocket {
  static last: FakeSocket;
  readyState = 1;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  closed = false;

  constructor(readonly url: string) {
    FakeSocket.last = this;
  }

  close() {
    this.closed = true;
  }

  send(job: Partial<Job>) {
    this.onmessage?.({ data: JSON.stringify(job) } as MessageEvent<string>);
  }

  closeWith(code: number) {
    this.onclose?.({ code } as CloseEvent);
  }
}

describe('jobSocketUrl', () => {
  it('выбирает ws или wss по протоколу страницы', () => {
    expect(jobSocketUrl({ protocol: 'http:', host: 'localhost:4200' }, 7)).toBe(
      'ws://localhost:4200/api/ws/jobs/7',
    );
    expect(jobSocketUrl({ protocol: 'https:', host: 'trader.local' }, 7)).toBe(
      'wss://trader.local/api/ws/jobs/7',
    );
  });
});

describe('isFinished', () => {
  it('завершёнными считаются succeeded, failed и cancelled', () => {
    expect(isFinished({ status: 'succeeded' })).toBe(true);
    expect(isFinished({ status: 'failed' })).toBe(true);
    expect(isFinished({ status: 'cancelled' })).toBe(true);
    expect(isFinished({ status: 'running' })).toBe(false);
  });
});

describe('JobsService.watch', () => {
  function setup() {
    TestBed.configureTestingModule({
      providers: [
        {
          provide: SOCKET_FACTORY,
          useValue: (url: string) => new FakeSocket(url),
        },
      ],
    });
    return TestBed.inject(JobsService);
  }

  it('отдаёт состояния и завершается, когда задача закончилась', () => {
    const seen: string[] = [];
    let completed = false;
    setup()
      .watch(5)
      .subscribe({
        next: (job) => seen.push(`${job.status}:${job.progress}`),
        complete: () => (completed = true),
      });
    const socket = FakeSocket.last;

    socket.send({ status: 'running', progress: 0.5 });
    socket.send({ status: 'succeeded', progress: 1 });
    socket.closeWith(1005);

    expect(socket.url).toContain('/api/ws/jobs/5');
    expect(seen).toEqual(['running:0.5', 'succeeded:1']);
    expect(completed).toBe(true);
  });

  it('несуществующая задача — ошибка с понятным текстом', () => {
    let message = '';
    setup()
      .watch(9)
      .subscribe({ error: (e: Error) => (message = e.message) });

    FakeSocket.last.closeWith(4404);

    expect(message).toContain('не найдена');
  });

  it('обрыв связи до завершения — ошибка', () => {
    let failed = false;
    setup()
      .watch(1)
      .subscribe({ error: () => (failed = true) });
    FakeSocket.last.send({ status: 'running' });

    FakeSocket.last.closeWith(1006);

    expect(failed).toBe(true);
  });

  it('отписка закрывает соединение', () => {
    const subscription = setup().watch(1).subscribe();
    const socket = FakeSocket.last;

    subscription.unsubscribe();

    expect(socket.closed).toBe(true);
  });
});
