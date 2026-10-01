import { inject, Injectable, signal } from '@angular/core';
import type {
  Backtest,
  BacktestLock,
  BacktestTrade,
  BacktestWindow,
  Job,
  Root,
} from '@trader/api-client';
import { ApiError, ApiService } from '../../core/api/api';
import { JobsService } from '../../core/jobs/jobs';
import {
  type BacktestForm,
  buildRequest,
  defaultForm,
  type MetricUnit,
} from './backtest-model';

const TRADES_LIMIT = 1000;
const HISTORY_LIMIT = 20;

/** Запуск бэктестов, ход задачи, результаты и блокировки test (ADR-0027). */
@Injectable()
export class BacktestStore {
  private readonly api = inject(ApiService);
  private readonly jobs = inject(JobsService);

  readonly roots = signal<Root[]>([]);
  readonly form = signal<BacktestForm>(defaultForm());
  readonly unit = signal<MetricUnit>('ticks');
  readonly current = signal<Backtest | null>(null);
  readonly trades = signal<BacktestTrade[]>([]);
  readonly windows = signal<BacktestWindow[]>([]);
  readonly history = signal<Backtest[]>([]);
  readonly locks = signal<BacktestLock[]>([]);
  readonly running = signal(false);
  readonly progress = signal<{ fraction: number; message: string } | null>(
    null,
  );
  readonly error = signal<string | null>(null);

  async init(): Promise<void> {
    try {
      this.roots.set(await this.api.call(this.api.client.GET('/roots')));
      const first = this.roots()[0];
      if (this.form().rootId === null && first) {
        this.patch({ rootId: first.id });
      }
      await Promise.all([this.loadHistory(), this.loadLocks()]);
    } catch (error) {
      this.error.set(message(error));
    }
  }

  patch(changes: Partial<BacktestForm>): void {
    this.form.update((form) => ({ ...form, ...changes }));
  }

  async loadHistory(): Promise<void> {
    this.history.set(
      await this.api.call(
        this.api.client.GET('/backtests', {
          params: { query: { limit: HISTORY_LIMIT } },
        }),
      ),
    );
  }

  async loadLocks(): Promise<void> {
    this.locks.set(
      await this.api.call(this.api.client.GET('/backtest-locks', {})),
    );
  }

  /** Запускает бэктест по форме и ждёт задачу; результат подгружается по завершении. */
  async run(): Promise<void> {
    const request = buildRequest(this.form());
    if (typeof request === 'string') {
      this.error.set(request);
      return;
    }
    this.error.set(null);
    this.running.set(true);
    this.progress.set({ fraction: 0, message: 'постановка в очередь' });
    try {
      const created = await this.api.call(
        this.api.client.POST('/backtests', { body: request }),
      );
      this.current.set(created);
      this.trades.set([]);
      this.windows.set([]);
      if (created.job_id !== null && created.job_id !== undefined) {
        await this.follow(created.job_id);
      }
      await this.open(created.id);
      await Promise.all([this.loadHistory(), this.loadLocks()]);
    } catch (error) {
      this.error.set(message(error));
    } finally {
      this.running.set(false);
      this.progress.set(null);
    }
  }

  /** Следит за задачей по WebSocket и показывает прогресс; ошибка связи — в `error`. */
  private follow(jobId: number): Promise<void> {
    return new Promise((resolve) => {
      this.jobs.watch(jobId).subscribe({
        next: (job: Job) =>
          this.progress.set({
            fraction: job.progress,
            message: job.progress_message ?? '',
          }),
        error: (error: unknown) => {
          this.error.set(message(error));
          resolve();
        },
        complete: () => resolve(),
      });
    });
  }

  /** Загружает результат, сделки и окна уже существующего бэктеста. */
  async open(id: number): Promise<void> {
    try {
      const backtest = await this.api.call(
        this.api.client.GET('/backtests/{experiment_id}', {
          params: { path: { experiment_id: id } },
        }),
      );
      this.current.set(backtest);
      if (backtest.status === 'failed') {
        this.error.set(backtest.error ?? 'Ошибка');
      } else if (backtest.status === 'succeeded') {
        this.error.set(null);
      } // queued/running: сообщение (например, обрыв связи) остаётся
      if (backtest.status !== 'succeeded') {
        this.trades.set([]);
        this.windows.set([]);
        return;
      }
      const [trades, windows] = await Promise.all([
        this.api.call(
          this.api.client.GET('/backtests/{experiment_id}/trades', {
            params: {
              path: { experiment_id: id },
              query: { limit: TRADES_LIMIT },
            },
          }),
        ),
        backtest.kind === 'walk_forward'
          ? this.api.call(
              this.api.client.GET('/backtests/{experiment_id}/windows', {
                params: { path: { experiment_id: id } },
              }),
            )
          : Promise.resolve([] as BacktestWindow[]),
      ]);
      this.trades.set(trades);
      this.windows.set(windows);
    } catch (error) {
      this.error.set(message(error));
    }
  }

  /** Явная разблокировка test-периода связки; причина попадает в журнал. */
  async unlock(lock: BacktestLock, note: string): Promise<boolean> {
    try {
      const done = await this.api.call(
        this.api.client.POST('/backtest-locks/unlock', {
          body: {
            root_id: lock.root_id,
            timeframe: lock.timeframe_code,
            family: lock.family,
            note,
          },
        }),
      );
      await this.loadLocks();
      return done.unlocked;
    } catch (error) {
      this.error.set(message(error));
      return false;
    }
  }
}

function message(error: unknown): string {
  return error instanceof ApiError || error instanceof Error
    ? error.message
    : String(error);
}
