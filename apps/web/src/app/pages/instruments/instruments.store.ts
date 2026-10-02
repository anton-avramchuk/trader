import { computed, inject, Injectable, signal } from '@angular/core';
import type {
  CandleLoad,
  ImporterTicker,
  Instrument,
  Job,
} from '@trader/api-client';
import { lastValueFrom } from 'rxjs';
import { ApiService } from '../../core/api/api';
import { JobsService } from '../../core/jobs/jobs';

/** Период загрузки свечей: даты `YYYY-MM-DD`, конец исключительно. */
export interface LoadPeriod {
  from: string;
  to: string;
}

/** Состояние и действия страницы Data → Instruments (ADR-0028). */
@Injectable()
export class InstrumentsStore {
  private readonly api = inject(ApiService);
  private readonly jobs = inject(JobsService);

  readonly instruments = signal<Instrument[]>([]);
  readonly selectedId = signal<number | null>(null);
  readonly tickers = signal<ImporterTicker[]>([]);
  readonly tickersError = signal<string | null>(null);
  readonly loads = signal<CandleLoad[]>([]);
  readonly loadJob = signal<Job | null>(null);
  readonly loading = signal(false);

  readonly selected = computed(
    () => this.instruments().find((i) => i.id === this.selectedId()) ?? null,
  );
  readonly available = computed(() =>
    this.tickers().filter((ticker) => !ticker.added),
  );
  readonly loadRunning = computed(() => {
    const job = this.loadJob();
    return (
      job !== null && (job.status === 'queued' || job.status === 'running')
    );
  });

  /** Список инструментов; если ничего не выбрано, выбирается `preferredTicker` (из адреса), иначе первый. */
  async load(preferredTicker: string | null = null): Promise<void> {
    this.loading.set(true);
    try {
      this.instruments.set(
        await this.api.call(this.api.client.GET('/instruments')),
      );
    } finally {
      this.loading.set(false);
    }
    if (this.selectedId() === null || !this.selected()) {
      const preferred = this.instruments().find(
        (i) => i.ticker === preferredTicker,
      );
      await this.select(preferred?.id ?? this.instruments()[0]?.id ?? null);
    }
  }

  async select(instrumentId: number | null): Promise<void> {
    this.selectedId.set(instrumentId);
    this.loadJob.set(null);
    this.loads.set([]);
    if (instrumentId !== null) {
      await this.loadJournal(instrumentId);
    }
  }

  /** Тикеры importer; недоступность сервиса — сообщение, а не падение страницы. */
  async loadTickers(): Promise<void> {
    this.tickersError.set(null);
    try {
      this.tickers.set(
        await this.api.call(this.api.client.GET('/importer/tickers')),
      );
    } catch (error) {
      this.tickers.set([]);
      this.tickersError.set((error as Error).message);
    }
  }

  async add(ticker: string, tickValue: number | null): Promise<void> {
    const created = await this.api.call(
      this.api.client.POST('/instruments', {
        body: { ticker, tick_value: tickValue },
      }),
    );
    await this.load();
    await this.select(created.id);
    await this.loadTickers();
  }

  async setTickValue(
    instrumentId: number,
    tickValue: number | null,
  ): Promise<void> {
    await this.api.call(
      this.api.client.PATCH('/instruments/{instrument_id}', {
        params: { path: { instrument_id: instrumentId } },
        body: { tick_value: tickValue },
      }),
    );
    await this.load();
  }

  async loadJournal(instrumentId: number): Promise<void> {
    this.loads.set(
      await this.api.call(
        this.api.client.GET('/instruments/{instrument_id}/loads', {
          params: { path: { instrument_id: instrumentId } },
        }),
      ),
    );
  }

  /** Ставит задачу `candles.load` и ждёт её конца; по итогу обновляет покрытие. */
  async loadCandles(
    instrumentId: number,
    period: LoadPeriod,
    timeframes: string[] | null,
  ): Promise<void> {
    const job = await this.api.call(
      this.api.client.POST('/instruments/{instrument_id}/load', {
        params: { path: { instrument_id: instrumentId } },
        body: {
          period_from: period.from,
          period_to: period.to,
          timeframes,
        },
      }),
    );
    this.loadJob.set(job);
    try {
      const last = await lastValueFrom(this.jobs.watch(job.id), {
        defaultValue: job,
      });
      this.loadJob.set(last);
    } catch (error) {
      this.loadJob.set({
        ...job,
        status: 'failed',
        error: (error as Error).message,
      });
    }
    await this.load();
    await this.loadJournal(instrumentId);
  }
}
