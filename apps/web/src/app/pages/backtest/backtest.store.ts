import { inject, Injectable, signal } from '@angular/core';
import type {
  Backtest,
  BacktestLock,
  BacktestTrade,
  BacktestWindow,
  Forecast,
  Job,
  OccurrenceOutcomes,
  Instrument,
} from '@trader/api-client';
import { ApiError, ApiService } from '../../core/api/api';
import { JobsService } from '../../core/jobs/jobs';
import { levelInfos } from '../chart/structure';
import {
  type BacktestForm,
  buildRequest,
  defaultForm,
  engineOf,
  engineRuns,
  type MetricUnit,
  type NearestLevel,
  nearestLevel,
} from './backtest-model';

/** Состояние drill-down выбранной сделки: каждый блок грузится независимо. */
export interface Drill {
  trade: BacktestTrade;
  loading: boolean;
  occurrence: OccurrenceOutcomes | null;
  level: NearestLevel | null;
  levelsFound: number;
  notes: string[];
  forecast: { loading: boolean; error: string | null; data: Forecast | null };
}

const TRADES_LIMIT = 1000;
const HISTORY_LIMIT = 20;

/** Запуск бэктестов, ход задачи, результаты и блокировки test (ADR-0027). */
@Injectable()
export class BacktestStore {
  private readonly api = inject(ApiService);
  private readonly jobs = inject(JobsService);

  readonly instruments = signal<Instrument[]>([]);
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
  readonly drill = signal<Drill | null>(null);
  private drillSequence = 0;

  async init(): Promise<void> {
    try {
      this.instruments.set(
        await this.api.call(this.api.client.GET('/instruments')),
      );
      const first = this.instruments()[0];
      if (this.form().instrumentId === null && first) {
        this.patch({ instrumentId: first.id });
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

  /**
   * Drill-down сделки: вхождение и режим на входе, ближайший уровень (по прогону
   * levels на момент входа) — сразу; прогноз на входе — по запросу (`loadForecast`).
   */
  async selectTrade(trade: BacktestTrade): Promise<void> {
    const seq = ++this.drillSequence;
    const backtest = this.current();
    const runs = engineRuns(backtest);
    const engine = engineOf(trade.ref);
    const notes: string[] = [];
    const base: Drill = {
      trade,
      loading: true,
      occurrence: null,
      level: null,
      levelsFound: 0,
      notes,
      forecast: { loading: false, error: null, data: null },
    };
    this.drill.set(base);
    const update = (changes: Partial<Drill>): void => {
      if (seq === this.drillSequence) {
        this.drill.update((d) => (d ? { ...d, ...changes } : d));
      }
    };
    const occurrenceRun = engine ? runs[engine] : undefined;
    const levelsRun = runs['levels'];
    await Promise.all([
      (async () => {
        if (occurrenceRun === undefined || !trade.ref) {
          notes.push('Прогон движка события не найден в версиях эксперимента');
          return;
        }
        try {
          update({
            occurrence: await this.api.call(
              this.api.client.GET('/stats/occurrence', {
                params: {
                  query: {
                    run_id: occurrenceRun,
                    key: trade.ref,
                    as_of: trade.entry_time,
                  },
                },
              }),
            ),
          });
        } catch (error) {
          notes.push(`Событие: ${message(error)}`);
        }
      })(),
      (async () => {
        if (levelsRun === undefined) {
          notes.push('Прогон уровней не найден в версиях эксперимента');
          return;
        }
        try {
          const events = await this.api.call(
            this.api.client.GET('/engine-runs/{run_id}/events', {
              params: {
                path: { run_id: levelsRun },
                query: { view: 'current', as_of: trade.entry_time },
              },
            }),
          );
          const levels = levelInfos(events).filter((l) => l.state === 'active');
          update({
            levelsFound: levels.length,
            level: nearestLevel(levels, trade.signal_price),
          });
        } catch (error) {
          notes.push(`Уровни: ${message(error)}`);
        }
      })(),
    ]);
    update({ loading: false, notes });
  }

  closeTrade(): void {
    this.drillSequence++;
    this.drill.set(null);
  }

  /** Прогноз (Empirical и KNN) на момент входа выбранной сделки — по запросу. */
  async loadForecast(): Promise<void> {
    const drill = this.drill();
    if (!drill) {
      return;
    }
    const seq = this.drillSequence;
    const runs = engineRuns(this.current());
    const engine = engineOf(drill.trade.ref);
    const queryRun = engine ? runs[engine] : undefined;
    const set = (forecast: Drill['forecast']): void => {
      if (seq === this.drillSequence) {
        this.drill.update((d) => (d ? { ...d, forecast } : d));
      }
    };
    if (queryRun === undefined || !drill.trade.ref) {
      set({
        loading: false,
        error: 'Прогон движка события не найден',
        data: null,
      });
      return;
    }
    set({ loading: true, error: null, data: null });
    try {
      const data = await this.api.call(
        this.api.client.GET('/forecast', {
          params: {
            query: {
              query_run_id: queryRun,
              run_id: Object.values(runs),
              key: drill.trade.ref,
              unit: 'atr',
              as_of: drill.trade.entry_time,
            },
          },
        }),
      );
      set({ loading: false, error: null, data });
    } catch (error) {
      set({ loading: false, error: message(error), data: null });
    }
  }

  /** Явная разблокировка test-периода связки; причина попадает в журнал. */
  async unlock(lock: BacktestLock, note: string): Promise<boolean> {
    try {
      const done = await this.api.call(
        this.api.client.POST('/backtest-locks/unlock', {
          body: {
            instrument_id: lock.instrument_id,
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
