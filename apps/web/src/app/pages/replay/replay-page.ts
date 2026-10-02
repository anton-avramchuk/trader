import {
  Component,
  computed,
  effect,
  inject,
  OnDestroy,
  OnInit,
  untracked,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { TuiButton } from '@taiga-ui/core';
import { TuiSegmented } from '@taiga-ui/kit/components/segmented';
import { UiDate, UiSelect } from '../../core/ui';
import { MskPipe } from '../../core/time/msk';
import { describeBar } from '../chart/chart-data';
import { IndicatorPanel } from '../chart/indicator-panel';
import { ProfileBar } from '../chart/profile-bar';
import { ProfilesStore } from '../chart/profiles.store';
import { IndicatorsStore } from '../chart/indicators.store';
import { PriceChart } from '../chart/price-chart';
import { StructurePanel } from '../chart/structure-panel';
import { levelReference } from '../chart/structure';
import { StructureStore } from '../chart/structure.store';
import {
  REPLAY_TIMEFRAMES,
  type ReplayTimeframe,
  ReplayStore,
  SPEEDS,
} from './replay.store';

const REFRESH_DELAY_MS = 250;

/** Visual replay: по одной свече или ускоренно; будущее скрыто, видно только известное на момент. */
@Component({
  selector: 'app-replay',
  imports: [
    FormsModule,
    IndicatorPanel,
    MskPipe,
    PriceChart,
    ProfileBar,
    StructurePanel,
    TuiButton,
    TuiSegmented,
    UiDate,
    UiSelect,
  ],
  providers: [ReplayStore, IndicatorsStore, ProfilesStore, StructureStore],
  template: `
    <h1>Replay</h1>

    <div class="card toolbar">
      <app-select
        label="Инструмент"
        aria-label="Инструмент"
        [options]="instrumentOptions()"
        [ngModel]="store.instrumentId()"
        (ngModelChange)="store.selectInstrument($event)"
      />
      <tui-segmented
        size="m"
        aria-label="Таймфрейм"
        [activeItemIndex]="timeframes.indexOf(store.timeframe())"
      >
        @for (tf of timeframes; track tf) {
          <button type="button" (click)="store.selectTimeframe(tf)">
            {{ tf }}
          </button>
        }
      </tui-segmented>
      <app-date label="Дата старта (МСК)" name="date" [(ngModel)]="date" />
      <button
        tuiButton
        type="button"
        size="m"
        [disabled]="!date || store.loading()"
        (click)="store.start(date)"
      >
        {{ store.timeline().length ? 'Перейти к дате' : 'Начать' }}
      </button>
      @if (store.loading()) {
        <span class="muted">Загрузка…</span>
      }
      <app-profile-bar
        class="profiles"
        [instrumentId]="store.instrumentId()"
        [chartTimeframe]="store.timeframe()"
        (timeframeRequested)="onProfileTimeframe($event)"
      />
    </div>

    <div
      class="card toolbar controls"
      role="toolbar"
      aria-label="Управление replay"
    >
      <button
        tuiButton
        type="button"
        size="s"
        appearance="secondary"
        iconStart="@tui.rotate-ccw"
        [disabled]="!ready()"
        (click)="store.restart()"
      >
        Сначала
      </button>
      <tui-segmented size="s" aria-label="Шаг назад">
        <button type="button" [disabled]="!ready()" (click)="store.move(-100)">
          −100
        </button>
        <button type="button" [disabled]="!ready()" (click)="store.move(-10)">
          −10
        </button>
        <button type="button" [disabled]="!ready()" (click)="store.move(-1)">
          ◀ Назад
        </button>
      </tui-segmented>
      @if (store.playing()) {
        <button
          tuiButton
          type="button"
          size="s"
          iconStart="@tui.pause"
          (click)="store.pause()"
        >
          Пауза
        </button>
      } @else {
        <button
          tuiButton
          type="button"
          size="s"
          iconStart="@tui.play"
          [disabled]="!ready() || store.atEnd()"
          (click)="store.play()"
        >
          Пуск
        </button>
      }
      <tui-segmented size="s" aria-label="Шаг вперёд">
        <button
          type="button"
          [disabled]="!ready() || store.atEnd()"
          (click)="store.move(1)"
        >
          Следующая ▶
        </button>
        <button
          type="button"
          [disabled]="!ready() || store.atEnd()"
          (click)="store.move(10)"
        >
          +10
        </button>
        <button
          type="button"
          [disabled]="!ready() || store.atEnd()"
          (click)="store.move(100)"
        >
          +100
        </button>
      </tui-segmented>
      <app-select
        label="Скорость"
        aria-label="Скорость"
        [options]="speedOptions"
        [ngModel]="store.speed()"
        (ngModelChange)="store.setSpeed($event)"
      />
    </div>

    <div class="workbench">
      <section class="stack">
        <app-indicator-panel [chartTimeframe]="store.timeframe()" />
        <p class="known" aria-live="polite">
          @if (store.asOf(); as t) {
            <strong>Система знает на {{ t | msk: 'full' }} МСК:</strong>
            баров {{ store.visible().length }} из
            {{ store.timeline().length }} (будущее скрыто).
            @if (store.atEnd()) {
              Данные закончились.
            }
          } @else {
            Выберите дату и нажмите «Начать».
          }
        </p>
        <p class="legend">{{ legend() }}</p>
        <div class="chart">
          <app-price-chart
            [candles]="store.visible()"
            [datasetKey]="store.datasetKey()"
            [follow]="true"
            [indicators]="indicators.series()"
            [overlay]="structure.overlay()"
          />
        </div>
        <div class="toolbar">
          <button
            tuiButton
            type="button"
            size="s"
            appearance="flat"
            [disabled]="!ready()"
            (click)="store.verifyWithServer()"
          >
            Сверить с серверным snapshot
          </button>
          @if (store.verify(); as v) {
            <span
              [class.bad]="v.mismatches > 0"
              [class.good]="v.mismatches === 0"
            >
              {{
                v.mismatches === 0
                  ? 'Совпадает'
                  : 'Расхождения: ' + v.mismatches
              }}
              (проверено {{ v.checked }} баров на
              {{ v.asOf | msk: 'full' }} МСК)
            </span>
          }
        </div>
      </section>
      <aside class="side card">
        <app-structure-panel [chartTimeframe]="store.timeframe()" />
      </aside>
    </div>
  `,
  styles: `
    .toolbar {
      margin-bottom: 1rem;
    }
    .toolbar .toolbar {
      margin-bottom: 0;
    }
    .profiles {
      margin-inline-start: auto;
    }
    .known,
    .legend {
      margin: 0;
      font-size: 0.9rem;
      min-height: 1.25rem;
    }
    .legend {
      font-family: monospace;
    }
    .chart {
      height: calc(100vh - 26rem);
      min-height: 24rem;
    }
    .good {
      color: #2e7d32;
    }
    .bad {
      color: var(--tui-text-negative);
    }
  `,
})
export class Replay implements OnInit, OnDestroy {
  private readonly route = inject(ActivatedRoute);
  protected readonly store = inject(ReplayStore);
  protected readonly indicators = inject(IndicatorsStore);
  protected readonly structure = inject(StructureStore);
  private refreshTimer: ReturnType<typeof setTimeout> | null = null;
  protected readonly timeframes: readonly ReplayTimeframe[] = REPLAY_TIMEFRAMES;
  protected readonly speedOptions = SPEEDS.map((s) => ({
    value: s,
    label: `${s} бар/с`,
  }));
  protected readonly instrumentOptions = computed(() =>
    this.store.instruments().map((i) => ({ value: i.id, label: i.ticker })),
  );
  protected date = '';

  protected readonly ready = computed(() => this.store.timeline().length > 0);

  protected readonly legend = computed(() => {
    const candle = this.store.visible().at(-1);
    return candle ? describeBar(candle) : '';
  });

  constructor() {
    // Индикаторы считаются «на момент знания» (as_of) — без заглядывания в будущее;
    // при быстром воспроизведении запросы склеиваются (последний выигрывает).
    effect(() => {
      const asOf = this.store.asOf();
      const first = this.store.visible()[0];
      const instrumentId = this.store.instrumentId();
      const timeframe = this.store.timeframe();
      if (!asOf || !first || instrumentId === null) {
        return;
      }
      untracked(() => {
        this.structure.setReference(levelReference(this.store.visible()));
        if (this.refreshTimer !== null) {
          clearTimeout(this.refreshTimer);
        }
        this.refreshTimer = setTimeout(() => {
          const series = { instrument_id: instrumentId };
          void this.indicators.refresh({
            ...series,
            chartTimeframe: timeframe,
            start: first.timestamp,
            asOf,
          });
          void this.structure.refresh({
            ...series,
            chartTimeframe: timeframe,
            asOf,
          });
        }, REFRESH_DELAY_MS);
      });
    });
  }

  ngOnInit(): void {
    void this.loadAndOpenLink();
    void this.indicators.loadCatalog();
  }

  /**
   * Ссылка из бэктеста (`?instrument=&tf=&date=`): выбирает инструмент и TF и начинает
   * воспроизведение с указанного дня — будущее скрыто, видно только известное.
   */
  private async loadAndOpenLink(): Promise<void> {
    await this.store.loadInstruments();
    const query = this.route.snapshot.queryParamMap;
    const instrument = Number(query.get('instrument'));
    const date = query.get('date');
    const timeframe = query.get('tf');
    if (
      !instrument ||
      !date ||
      !this.store.instruments().some((i) => i.id === instrument)
    ) {
      return;
    }
    this.date = date;
    this.store.selectInstrument(instrument);
    if ((REPLAY_TIMEFRAMES as readonly string[]).includes(timeframe ?? '')) {
      this.store.selectTimeframe(timeframe as ReplayTimeframe);
    }
    await this.store.start(date);
  }

  ngOnDestroy(): void {
    if (this.refreshTimer !== null) {
      clearTimeout(this.refreshTimer);
    }
  }

  protected onProfileTimeframe(timeframe: string): void {
    if ((REPLAY_TIMEFRAMES as readonly string[]).includes(timeframe)) {
      this.store.selectTimeframe(timeframe as ReplayTimeframe);
    }
  }
}
