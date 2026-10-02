import { computed, inject, Injectable, signal } from '@angular/core';
import type { Candle, Instrument } from '@trader/api-client';
import { ApiService } from '../../core/api/api';
import { type ChartTimeframe, prependCandles } from './chart-data';

/** Сколько свечей запрашивается за раз (первая загрузка и подгрузка истории). */
export const PAGE_SIZE = 1500;

/** Предохранитель: не больше стольких страниц истории ради перехода к дате. */
const MAX_PAGES_TO_DATE = 40;

@Injectable()
export class ChartStore {
  private readonly api = inject(ApiService);

  readonly instruments = signal<Instrument[]>([]);
  readonly instrumentId = signal<number | null>(null);
  readonly timeframe = signal<ChartTimeframe>('1d');

  readonly candles = signal<Candle[]>([]);
  readonly hasOlder = signal(false);
  readonly loading = signal(false);

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
