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
import { TuiButton } from '@taiga-ui/core';
import type { Candle } from '@trader/api-client';
import { type ChartTimeframe, describeBar, TIMEFRAMES } from './chart-data';
import { ChartStore } from './chart.store';
import { IndicatorPanel } from './indicator-panel';
import { ProfileBar } from './profile-bar';
import { ProfilesStore } from './profiles.store';
import { IndicatorsStore } from './indicators.store';
import { PriceChart } from './price-chart';
import { StructurePanel } from './structure-panel';
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
    TuiButton,
  ],
  providers: [ChartStore, IndicatorsStore, ProfilesStore, StructureStore],
  template: `
    <h1>Chart</h1>

    <div class="inline">
      <label class="check">
        Инструмент:
        <select
          [ngModel]="store.instrumentId()"
          (ngModelChange)="store.selectInstrument($event)"
        >
          @for (i of store.instruments(); track i.id) {
            <option [ngValue]="i.id">{{ i.ticker }}</option>
          }
        </select>
      </label>
      <div class="tabs" role="tablist">
        @for (tf of timeframes; track tf) {
          <button
            tuiButton
            type="button"
            size="xs"
            role="tab"
            [appearance]="store.timeframe() === tf ? 'primary' : 'secondary'"
            (click)="store.selectTimeframe(tf)"
          >
            {{ tf }}
          </button>
        }
      </div>
      @if (store.loading()) {
        <span class="status">Загрузка…</span>
      }
    </div>

    <app-profile-bar
      [instrumentId]="store.instrumentId()"
      [chartTimeframe]="store.timeframe()"
      (timeframeRequested)="onProfileTimeframe($event)"
    />
    <app-indicator-panel [chartTimeframe]="store.timeframe()" />
    <app-structure-panel [chartTimeframe]="store.timeframe()" />

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

    <p class="hint">
      Время — МСК. Бледные свечи — неполные бары (обрезаны сессией, клирингом
      или границей сетки). Стрелки — роллы continuous-серии; цены прошлых
      контрактов приведены к масштабу текущего.
      @if (store.hasOlder()) {
        Прокрутите влево — подгрузится более ранняя история.
      }
    </p>
    <p class="hint">
      Графики:
      <a href="https://www.tradingview.com/" target="_blank" rel="noopener"
        >TradingView Lightweight Charts™</a
      >
    </p>
  `,
  styles: `
    .inline {
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      align-items: center;
      margin-bottom: 0.75rem;
    }
    .tabs {
      display: flex;
      gap: 0.25rem;
    }
    .check {
      display: flex;
      gap: 0.4rem;
      align-items: center;
    }
    .chart {
      height: 60vh;
      min-height: 26rem;
    }
    .legend {
      min-height: 1.5rem;
      font-family: monospace;
      font-size: 0.85rem;
    }
    .hint,
    .status {
      opacity: 0.7;
      font-size: 0.85rem;
    }
  `,
})
export class Chart implements OnInit {
  protected readonly store = inject(ChartStore);
  protected readonly indicators = inject(IndicatorsStore);
  protected readonly structure = inject(StructureStore);
  protected readonly timeframes = TIMEFRAMES;
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
