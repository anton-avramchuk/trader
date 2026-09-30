import { Component, computed, inject, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TuiButton } from '@taiga-ui/core';
import type { Candle } from '@trader/api-client';
import { describeBar, TIMEFRAMES } from './chart-data';
import { ChartStore, type Target } from './chart.store';
import { PriceChart } from './price-chart';

/** Chart: свечи и объём continuous-серии или контракта, роллы, подгрузка истории. */
@Component({
  selector: 'app-chart',
  imports: [FormsModule, PriceChart, TuiButton],
  providers: [ChartStore],
  template: `
    <h1>Chart</h1>

    <div class="inline">
      <label class="check">
        Инструмент:
        <select
          [ngModel]="store.rootId()"
          (ngModelChange)="store.selectRoot($event)"
        >
          @for (r of store.roots(); track r.id) {
            <option [ngValue]="r.id">{{ r.code }}</option>
          }
        </select>
      </label>
      <label class="check">
        Серия:
        <select [ngModel]="targetKey()" (ngModelChange)="onTarget($event)">
          <option value="root">Continuous (склейка ratio)</option>
          @for (c of store.contracts(); track c.id) {
            <option [value]="'c' + c.id">
              {{ c.secid ?? c.expiration_date }} (в своих ценах)
            </option>
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

    <p class="legend">{{ legend() }}</p>

    <div class="chart">
      <app-price-chart
        [candles]="store.candles()"
        [rolls]="store.rolls()"
        [labels]="store.labels()"
        [datasetKey]="store.datasetKey()"
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
  protected readonly timeframes = TIMEFRAMES;
  protected readonly hovered = signal<Candle | null>(null);

  protected readonly targetKey = computed(() => {
    const target = this.store.target();
    return target.kind === 'root' ? 'root' : `c${target.id}`;
  });

  /** Легенда: бар под курсором, иначе последний бар. */
  protected readonly legend = computed(() => {
    const candle = this.hovered() ?? this.store.candles().at(-1);
    return candle ? describeBar(candle, this.store.labels()) : '';
  });

  ngOnInit(): void {
    void this.store.loadRoots();
  }

  protected onTarget(key: string): void {
    const target: Target =
      key === 'root'
        ? { kind: 'root' }
        : { kind: 'contract', id: Number(key.slice(1)) };
    void this.store.selectTarget(target);
  }
}
