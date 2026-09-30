import { inject, Injectable, InjectionToken } from '@angular/core';
import type { Job } from '@trader/api-client';
import { Observable } from 'rxjs';

export const TERMINAL_STATUSES = ['succeeded', 'failed', 'cancelled'];
const NORMAL_CLOSURE = 1000;
const JOB_NOT_FOUND = 4404;

export type SocketFactory = (url: string) => WebSocket;

export const SOCKET_FACTORY = new InjectionToken<SocketFactory>(
  'SOCKET_FACTORY',
  { providedIn: 'root', factory: () => (url) => new WebSocket(url) },
);

/** Адрес WebSocket задачи на текущем хосте (`/api/ws/jobs/{id}`, dev-сервер проксирует). */
export function jobSocketUrl(
  location: Pick<Location, 'protocol' | 'host'>,
  jobId: number,
): string {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  return `${scheme}://${location.host}/api/ws/jobs/${jobId}`;
}

export function isFinished(job: Pick<Job, 'status'>): boolean {
  return TERMINAL_STATUSES.includes(job.status);
}

@Injectable({ providedIn: 'root' })
export class JobsService {
  private readonly socketFactory = inject(SOCKET_FACTORY);

  /**
   * Состояние задачи по мере изменения; поток завершается, когда задача
   * закончилась. Если задачи нет (код 4404) или связь оборвалась — ошибка.
   */
  watch(jobId: number): Observable<Job> {
    return new Observable<Job>((subscriber) => {
      const socket = this.socketFactory(jobSocketUrl(window.location, jobId));
      let finished = false;
      socket.onmessage = (event: MessageEvent<string>) => {
        const job = JSON.parse(event.data) as Job;
        finished = isFinished(job);
        subscriber.next(job);
      };
      socket.onerror = () =>
        subscriber.error(new Error('Нет связи с сервером'));
      socket.onclose = (event: CloseEvent) => {
        if (event.code === JOB_NOT_FOUND) {
          subscriber.error(new Error(`Задача ${jobId} не найдена`));
        } else if (finished || event.code === NORMAL_CLOSURE) {
          subscriber.complete();
        } else {
          subscriber.error(new Error('Соединение с сервером прервано'));
        }
      };
      return () => {
        socket.onmessage = socket.onerror = socket.onclose = null;
        if (socket.readyState <= WebSocket.OPEN) {
          socket.close();
        }
      };
    });
  }
}
