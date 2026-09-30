import { computed, inject, Injectable, OnDestroy, signal } from '@angular/core';
import type { Candle, Contract, Root, Roll } from '@trader/api-client';
import { ApiService } from '../../core/api/api';
import { mergeRolls } from '../chart/chart-data';
import {
  compareBars,
  clampCursor,
  knownAt,
  knownRolls,
  mskDate,
  mskMidnightUtc,
  visibleCandles,
} from './replay';

export const REPLAY_TIMEFRAMES = ['15m', '1h', '4h', '1d'] as const;
export type ReplayTimeframe = (typeof REPLAY_TIMEFRAMES)[number];

/** Скорость воспроизведения, баров в секунду. */
export const SPEEDS = [1, 2, 5, 10, 30, 100] as const;

/** Баров истории слева от точки старта и размер страницы будущих баров. */
export const LOOKBACK = 300;
export const FORWARD_PAGE = 5000;
const TICK_MS = 100;
const VERIFY_BARS = 50;

export type ReplayTarget = { kind: 'root' } | { kind: 'contract'; id: number };

export interface VerifyResult {
  checked: number;
  mismatches: number;
  asOf: string;
}

/** Состояние Visual replay: таймлайн загружен один раз, курсор — на клиенте. */
@Injectable()
export class ReplayStore implements OnDestroy {
  private readonly api = inject(ApiService);

  readonly roots = signal<Root[]>([]);
  readonly contracts = signal<Contract[]>([]);
  readonly rootId = signal<number | null>(null);
  readonly target = signal<ReplayTarget>({ kind: 'root' });
  readonly timeframe = signal<ReplayTimeframe>('1h');
  readonly startDate = signal('');

  readonly timeline = signal<Candle[]>([]);
  readonly rolls = signal<Roll[]>([]);
  /** Сколько баров видно (курсор). */
  readonly cursor = signal(0);
  /** Начальная позиция курсора после загрузки (для Restart). */
  readonly origin = signal(0);
  readonly hasMoreForward = signal(false);
  readonly playing = signal(false);
  readonly speed = signal<number>(5);
  readonly loading = signal(false);
  readonly verify = signal<VerifyResult | null>(null);

  private forwardFrom: string | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;

  readonly visible = computed(() =>
    visibleCandles(this.timeline(), this.rolls(), this.cursor()),
  );
  readonly known = computed(() =>
    knownRolls(this.timeline(), this.rolls(), this.cursor()),
  );
  /** Момент, до которого система «знает» рынок. */
  readonly asOf = computed(() => knownAt(this.timeline(), this.cursor()));
  readonly atEnd = computed(
    () => this.cursor() >= this.timeline().length && !this.hasMoreForward(),
  );
  readonly labels = computed(() =>
    Object.fromEntries(
      this.contracts().map((c) => [c.id, c.secid ?? c.expiration_date]),
    ),
  );
  readonly datasetKey = computed(
    () =>
      `${this.rootId()}:${JSON.stringify(this.target())}:${this.timeframe()}:${this.startDate()}`,
  );

  ngOnDestroy(): void {
    this.pause();
  }

  // --- справочники -----------------------------------------------------------

  async loadRoots(): Promise<void> {
    this.roots.set(await this.api.call(this.api.client.GET('/roots')));
    const first = this.roots()[0];
    if (this.rootId() === null && first) {
      await this.selectRoot(first.id);
    }
  }

  async selectRoot(rootId: number): Promise<void> {
    this.pause();
    this.rootId.set(rootId);
    this.target.set({ kind: 'root' });
    this.contracts.set(
      await this.api.call(
        this.api.client.GET('/roots/{root_id}/contracts', {
          params: { path: { root_id: rootId } },
        }),
      ),
    );
    this.clear();
  }

  selectTarget(target: ReplayTarget): void {
    this.pause();
    this.target.set(target);
    this.clear();
  }

  selectTimeframe(timeframe: ReplayTimeframe): void {
    this.pause();
    this.timeframe.set(timeframe);
    this.clear();
  }

  private clear(): void {
    this.timeline.set([]);
    this.rolls.set([]);
    this.cursor.set(0);
    this.origin.set(0);
    this.hasMoreForward.set(false);
    this.verify.set(null);
  }

  private selection(): { root_id?: number; contract_id?: number } {
    const target = this.target();
    if (target.kind === 'contract') {
      return { contract_id: target.id };
    }
    const rootId = this.rootId();
    return rootId === null ? {} : { root_id: rootId };
  }

  // --- запуск и навигация ------------------------------------------------------

  /** Начать (или перейти к дате, Jump to date): история слева, будущее скрыто. */
  async start(date: string): Promise<void> {
    if (!date || this.rootId() === null) {
      return;
    }
    this.pause();
    this.startDate.set(date);
    const asOf = mskMidnightUtc(date);
    const selection = this.selection();
    const timeframe = this.timeframe();
    this.loading.set(true);
    try {
      const [past, future] = await Promise.all([
        this.api.call(
          this.api.client.GET('/candles', {
            params: {
              query: {
                ...selection,
                timeframe,
                end: asOf,
                limit: LOOKBACK,
                tail: true,
              },
            },
          }),
        ),
        this.api.call(
          this.api.client.GET('/candles', {
            params: {
              query: {
                ...selection,
                timeframe,
                start: asOf,
                limit: FORWARD_PAGE,
              },
            },
          }),
        ),
      ]);
      this.timeline.set([...past.candles, ...future.candles]);
      this.rolls.set(mergeRolls(past.rolls, future.rolls));
      this.origin.set(past.candles.length);
      this.cursor.set(past.candles.length);
      this.forwardFrom = future.next_start ?? null;
      this.hasMoreForward.set(future.truncated);
      this.verify.set(null);
    } finally {
      this.loading.set(false);
    }
  }

  /** Сдвиг курсора на `delta` баров (вперёд или назад); вперёд подгружает будущее по мере надобности. */
  async move(delta: number): Promise<void> {
    if (delta > 0) {
      const need = this.cursor() + delta;
      if (need > this.timeline().length && this.hasMoreForward()) {
        await this.loadMoreForward();
      }
    }
    this.cursor.update((c) => clampCursor(c + delta, this.timeline().length));
    if (this.atEnd()) {
      this.pause();
    }
  }

  private async loadMoreForward(): Promise<void> {
    if (!this.forwardFrom || this.loading()) {
      return;
    }
    this.loading.set(true);
    try {
      const page = await this.api.call(
        this.api.client.GET('/candles', {
          params: {
            query: {
              ...this.selection(),
              timeframe: this.timeframe(),
              start: this.forwardFrom,
              limit: FORWARD_PAGE,
            },
          },
        }),
      );
      this.timeline.update((current) => [...current, ...page.candles]);
      this.rolls.update((current) => mergeRolls(current, page.rolls));
      this.forwardFrom = page.next_start ?? null;
      this.hasMoreForward.set(page.truncated);
    } finally {
      this.loading.set(false);
    }
  }

  restart(): void {
    this.pause();
    this.cursor.set(this.origin());
    this.verify.set(null);
  }

  // --- воспроизведение ---------------------------------------------------------

  play(): void {
    if (this.timer !== null || this.atEnd() || !this.timeline().length) {
      return;
    }
    this.playing.set(true);
    const perTick = Math.max(1, Math.round((this.speed() * TICK_MS) / 1000));
    const interval = Math.max(TICK_MS, Math.round(1000 / this.speed()));
    this.timer = setInterval(
      () => void this.move(perTick),
      Math.min(interval, 1000),
    );
  }

  pause(): void {
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
    this.playing.set(false);
  }

  setSpeed(speed: number): void {
    this.speed.set(speed);
    if (this.playing()) {
      this.pause();
      this.play();
    }
  }

  // --- сверка с сервером ---------------------------------------------------------

  /**
   * Сверяет последние показанные бары с серверным `snapshot?as_of=…`: клиентская
   * логика «что видно на момент t» должна совпасть с серверной.
   */
  async verifyWithServer(): Promise<void> {
    const asOf = this.asOf();
    if (asOf === null) {
      return;
    }
    const snapshot = await this.api.call(
      this.api.client.GET('/snapshot', {
        params: {
          query: {
            ...this.selection(),
            timeframe: this.timeframe(),
            as_of: asOf,
            limit: VERIFY_BARS,
          },
        },
      }),
    );
    const result = compareBars(this.visible(), snapshot.candles);
    this.verify.set({ ...result, asOf });
  }

  get startDateOrToday(): string {
    return this.startDate() || mskDate(new Date().toISOString());
  }
}
