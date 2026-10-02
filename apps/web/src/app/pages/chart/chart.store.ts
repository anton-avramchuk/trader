import { computed, inject, Injectable, signal } from '@angular/core';
import type { Candle, CandlesResponse, Instrument } from '@trader/api-client';
import { lastValueFrom } from 'rxjs';
import { ApiService } from '../../core/api/api';
import { JobsService } from '../../core/jobs/jobs';
import { catchUpPeriod, mergeCandles } from './catch-up';
import { type ChartTimeframe, prependCandles } from './chart-data';

/** Сколько свечей запрашивается за раз (первая загрузка и подгрузка истории). */
export const PAGE_SIZE = 1500;

/** Предохранитель: не больше стольких страниц истории ради перехода к дате. */
const MAX_PAGES_TO_DATE = 40;

@Injectable()
export class ChartStore {
  private readonly api = inject(ApiService);
  private readonly jobs = inject(JobsService);

  readonly instruments = signal<Instrument[]>([]);
  readonly instrumentId = signal<number | null>(null);
  readonly timeframe = signal<ChartTimeframe>('1d');

  readonly candles = signal<Candle[]>([]);
  readonly hasOlder = signal(false);
  readonly loading = signal(false);
  /** Догрузка свечей из importer: идёт ли и чем закончилась. */
  readonly catchingUp = signal(false);
  readonly catchUpMessage = signal<string | null>(null);

  /** Меняется при смене инструмента/TF — график начинается заново. */
  readonly datasetKey = computed(
    () => `${this.instrumentId()}:${this.timeframe()}`,
  );

  /** Список инструментов; выбирается `preferred` (запомненный), иначе первый. */
  async loadInstruments(preferred: number | null = null): Promise<void> {
    this.instruments.set(
      await this.api.call(this.api.client.GET('/instruments')),
    );
    const pick =
      this.instruments().find((i) => i.id === preferred) ??
      this.instruments()[0];
    if (this.instrumentId() === null && pick) {
      await this.selectInstrument(pick.id);
    }
  }

  async selectInstrument(instrumentId: number): Promise<void> {
    this.instrumentId.set(instrumentId);
    await this.reload();
  }

  async selectTimeframe(timeframe: ChartTimeframe): Promise<void> {
    this.timeframe.set(timeframe);
    await this.reload();
  }

  /** Последние свечи выбранного инструмента и таймфрейма. */
  async reload(): Promise<void> {
    this.candles.set([]);
    this.hasOlder.set(false);
    const instrumentId = this.instrumentId();
    if (instrumentId === null) {
      return;
    }
    const seq = this.begin();
    try {
      const page = await this.api.call(
        this.api.client.GET('/candles', {
          params: {
            query: {
              instrument_id: instrumentId,
              timeframe: this.timeframe(),
              limit: PAGE_SIZE,
              tail: true,
            },
          },
        }),
      );
      if (seq === this.sequence) {
        this.candles.set(page.candles);
        this.hasOlder.set(page.truncated);
      }
    } finally {
      if (seq === this.sequence) {
        this.loading.set(false);
      }
    }
  }

  /** Подгружает страницу истории левее уже показанных свечей. */
  async loadOlder(): Promise<void> {
    const first = this.candles()[0];
    const instrumentId = this.instrumentId();
    if (!first || instrumentId === null || !this.hasOlder() || this.loading()) {
      return;
    }
    const seq = this.begin();
    try {
      const page = await this.api.call(
        this.api.client.GET('/candles', {
          params: {
            query: {
              instrument_id: instrumentId,
              timeframe: this.timeframe(),
              limit: PAGE_SIZE,
              tail: true,
              end: first.timestamp,
            },
          },
        }),
      );
      if (seq === this.sequence) {
        this.candles.update((current) => prependCandles(page.candles, current));
        this.hasOlder.set(page.truncated);
      }
    } finally {
      if (seq === this.sequence) {
        this.loading.set(false);
      }
    }
  }

  /**
   * «Догрузить»: свечи от последней загруженной даты до сегодняшнего дня из importer
   * (задача `candles.load`), затем новые бары добавляются на график. Истина, если
   * загрузка прошла.
   */
  async catchUp(): Promise<boolean> {
    const instrumentId = this.instrumentId();
    const instrument = this.instruments().find((i) => i.id === instrumentId);
    if (!instrument || this.catchingUp()) {
      return false;
    }
    const period = catchUpPeriod(instrument.coverage);
    if (!period) {
      this.catchUpMessage.set(
        'Свечей ещё нет — загрузите период на странице Instruments.',
      );
      return false;
    }
    this.catchingUp.set(true);
    this.catchUpMessage.set(`Загружаем с ${period.from}…`);
    try {
      const job = await this.api.call(
        this.api.client.POST('/instruments/{instrument_id}/load', {
          params: { path: { instrument_id: instrument.id } },
          body: {
            period_from: period.from,
            period_to: period.to,
            timeframes: null,
          },
        }),
      );
      const last = await lastValueFrom(this.jobs.watch(job.id), {
        defaultValue: job,
      });
      if (last.status !== 'succeeded') {
        throw new Error(last.error ?? `Загрузка не выполнена: ${last.status}`);
      }
      const loaded = Object.values(
        (last.result as { loaded?: Record<string, number> } | null)?.loaded ??
          {},
      ).reduce((sum, n) => sum + n, 0);
      this.instruments.set(
        await this.api.call(this.api.client.GET('/instruments')),
      );
      await this.appendNewer();
      this.catchUpMessage.set(`Догружено свечей: ${loaded}`);
      return true;
    } catch (error) {
      this.catchUpMessage.set(
        error instanceof Error ? error.message : String(error),
      );
      return false;
    } finally {
      this.catchingUp.set(false);
    }
  }

  /** Добавляет к графику свечи новее последней показанной (заменяя совпавшие). */
  private async appendNewer(): Promise<void> {
    const instrumentId = this.instrumentId();
    const last = this.candles().at(-1);
    if (instrumentId === null || !last) {
      return;
    }
    let start: string | undefined = last.timestamp;
    for (let guard = 0; start && guard < MAX_PAGES_TO_DATE; guard++) {
      const page: CandlesResponse = await this.api.call(
        this.api.client.GET('/candles', {
          params: {
            query: {
              instrument_id: instrumentId,
              timeframe: this.timeframe(),
              limit: PAGE_SIZE,
              start,
            },
          },
        }),
      );
      this.candles.update((current) => mergeCandles(current, page.candles));
      start = page.truncated ? (page.next_start ?? undefined) : undefined;
    }
  }

  /** Подгружает историю, пока самая ранняя свеча не станет не позже `timestamp` (или история не кончится). */
  async loadUntil(timestamp: string): Promise<void> {
    const target = new Date(timestamp).getTime();
    for (let guard = 0; guard < MAX_PAGES_TO_DATE; guard++) {
      const first = this.candles()[0];
      if (!first || new Date(first.timestamp).getTime() <= target) {
        return;
      }
      const before = this.candles().length;
      await this.loadOlder();
      if (!this.hasOlder() && this.candles().length === before) {
        return;
      }
    }
  }

  // Ответ на устаревший запрос (сменили TF, пока грузилось) не применяется.
  private sequence = 0;

  private begin(): number {
    this.loading.set(true);
    return ++this.sequence;
  }
}
