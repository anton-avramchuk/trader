import {
  Component,
  computed,
  effect,
  inject,
  OnInit,
  signal,
  untracked,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TuiSegmented } from '@taiga-ui/kit/components/segmented';
import { UiSelect } from '../../core/ui';
import type { Candle } from '@trader/api-client';
import { type ChartTimeframe, describeBar, TIMEFRAMES } from './chart-data';
import { ChartStore } from './chart.store';
import { IndicatorPanel } from './indicator-panel';
import { ProfileBar } from './profile-bar';
import { ProfilesStore } from './profiles.store';
import { IndicatorsStore } from './indicators.store';
import { PriceChart } from './price-chart';
import { StructurePanel } from './structure-panel';
import { levelReference } from './structure';
import { StructureStore } from './structure.store';

/** Chart: свечи и объём инструмента, слои структуры, подгрузка истории. */
@Component({
  selector: 'app-chart',
  imports: [
    FormsModule,
    IndicatorPanel,
    PriceChart,
    ProfileBar,
    StructurePanel,
    TuiSegmented,
    UiSelect,
  ],
  providers: [ChartStore, IndicatorsStore, ProfilesStore, StructureStore],
  template: `
    <h1>Chart</h1>

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

    <div class="workbench">
      <section class="stack">
        <app-indicator-panel [chartTimeframe]="store.timeframe()" />
        <p class="legend">{{ legend() }}</p>
        <div class="chart">
          <app-price-chart
            [candles]="store.candles()"
            [datasetKey]="store.datasetKey()"
            [indicators]="indicators.series()"
            [overlay]="structure.overlay()"
            (pointPicked)="structure.pickPoint($event)"
            (needOlder)="store.loadOlder()"
            (hover)="hovered.set($event)"
          />
        </div>
        <p class="muted">
          Время — МСК. Свечи показаны как есть, без склеек и поправок.
          @if (store.hasOlder()) {
            Прокрутите влево — подгрузится более ранняя история.
          }
          Графики:
          <a href="https://www.tradingview.com/" target="_blank" rel="noopener"
            >TradingView Lightweight Charts™</a
          >
        </p>
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
    .profiles {
      margin-inline-start: auto;
    }
    .chart {
      height: calc(100vh - 17rem);
      min-height: 26rem;
    }
    .legend {
      min-height: 1.25rem;
      margin: 0;
      font-family: monospace;
      font-size: 0.85rem;
    }
  `,
})
export class Chart implements OnInit {
  protected readonly store = inject(ChartStore);
  protected readonly indicators = inject(IndicatorsStore);
  protected readonly structure = inject(StructureStore);
  protected readonly timeframes = TIMEFRAMES;
  protected readonly instrumentOptions = computed(() =>
    this.store.instruments().map((i) => ({ value: i.id, label: i.ticker })),
  );
  protected readonly hovered = signal<Candle | null>(null);

  /** Легенда: бар под курсором, иначе последний бар. */
  protected readonly legend = computed(() => {
    const candle = this.hovered() ?? this.store.candles().at(-1);
    return candle ? describeBar(candle) : '';
  });

  constructor() {
    // Индикаторы пересчитываются при смене серии/TF и подгрузке истории.
    effect(() => {
      const candles = this.store.candles();
      const timeframe = this.store.timeframe();
      const instrumentId = this.store.instrumentId();
      const first = candles[0];
      if (!first || instrumentId === null) {
        return;
      }
      const series = { instrument_id: instrumentId };
      untracked(() => {
        this.structure.setReference(levelReference(candles));
        void this.indicators.refresh({
          ...series,
          chartTimeframe: timeframe,
          start: first.timestamp,
        });
        void this.structure.refresh({ ...series, chartTimeframe: timeframe });
      });
    });
  }

  ngOnInit(): void {
    void this.store.loadInstruments();
    void this.indicators.loadCatalog();
  }

  protected onProfileTimeframe(timeframe: string): void {
    if ((TIMEFRAMES as readonly string[]).includes(timeframe)) {
      void this.store.selectTimeframe(timeframe as ChartTimeframe);
    }
  }
}
