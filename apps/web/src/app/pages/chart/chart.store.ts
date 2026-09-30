import { computed, inject, Injectable, signal } from '@angular/core';
import type { Candle, Contract, Root, Roll } from '@trader/api-client';
import { ApiService } from '../../core/api/api';
import { type ChartTimeframe, mergeRolls, prependCandles } from './chart-data';

/** Сколько баров запрашивается за раз (первая загрузка и подгрузка истории). */
export const PAGE_SIZE = 1500;

/** Что показывать: continuous-серию root или конкретный контракт. */
export type Target = { kind: 'root' } | { kind: 'contract'; id: number };

@Injectable()
export class ChartStore {
  private readonly api = inject(ApiService);

  readonly roots = signal<Root[]>([]);
  readonly contracts = signal<Contract[]>([]);
  readonly rootId = signal<number | null>(null);
  readonly target = signal<Target>({ kind: 'root' });
  readonly timeframe = signal<ChartTimeframe>('1d');

  readonly candles = signal<Candle[]>([]);
  readonly rolls = signal<Roll[]>([]);
  readonly hasOlder = signal(false);
  readonly loading = signal(false);

  /** SECID контрактов для подписей роллов и легенды. */
  readonly labels = computed(() =>
    Object.fromEntries(
      this.contracts().map((c) => [c.id, c.secid ?? c.expiration_date]),
    ),
  );

  /** Меняется при смене инструмента/TF — график начинается заново. */
  readonly datasetKey = computed(() => {
    const target = this.target();
    return `${this.rootId()}:${target.kind === 'root' ? 'root' : target.id}:${this.timeframe()}`;
  });

  async loadRoots(): Promise<void> {
    this.roots.set(await this.api.call(this.api.client.GET('/roots')));
    const first = this.roots()[0];
    if (this.rootId() === null && first) {
      await this.selectRoot(first.id);
    }
  }

  async selectRoot(rootId: number): Promise<void> {
    this.rootId.set(rootId);
    this.target.set({ kind: 'root' });
    this.contracts.set(
      await this.api.call(
        this.api.client.GET('/roots/{root_id}/contracts', {
          params: { path: { root_id: rootId } },
        }),
      ),
    );
    await this.reload();
  }

  async selectTarget(target: Target): Promise<void> {
    this.target.set(target);
    await this.reload();
  }

  async selectTimeframe(timeframe: ChartTimeframe): Promise<void> {
    this.timeframe.set(timeframe);
    await this.reload();
  }

  private selection(): { root_id?: number; contract_id?: number } {
    const target = this.target();
    if (target.kind === 'contract') {
      return { contract_id: target.id };
    }
    const rootId = this.rootId();
    return rootId === null ? {} : { root_id: rootId };
  }

  /** Последние бары выбранного инструмента и таймфрейма. */
  async reload(): Promise<void> {
    this.candles.set([]);
    this.rolls.set([]);
    this.hasOlder.set(false);
    if (this.rootId() === null) {
      return;
    }
    const seq = this.begin();
    try {
      const page = await this.api.call(
        this.api.client.GET('/candles', {
          params: {
            query: {
              ...this.selection(),
              timeframe: this.timeframe(),
              limit: PAGE_SIZE,
              tail: true,
            },
          },
        }),
      );
      if (seq === this.sequence) {
        this.candles.set(page.candles);
        this.rolls.set(page.rolls);
        this.hasOlder.set(page.truncated);
      }
    } finally {
      if (seq === this.sequence) {
        this.loading.set(false);
      }
    }
  }

  /** Подгружает страницу истории левее уже показанных баров. */
  async loadOlder(): Promise<void> {
    const first = this.candles()[0];
    if (!first || !this.hasOlder() || this.loading()) {
      return;
    }
    const seq = this.begin();
    try {
      const page = await this.api.call(
        this.api.client.GET('/candles', {
          params: {
            query: {
              ...this.selection(),
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
        this.rolls.update((current) => mergeRolls(current, page.rolls));
        this.hasOlder.set(page.truncated);
      }
    } finally {
      if (seq === this.sequence) {
        this.loading.set(false);
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
