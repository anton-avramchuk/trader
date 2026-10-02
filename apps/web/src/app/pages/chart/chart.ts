import {
  Component,
  computed,
  effect,
  inject,
  OnInit,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { TuiButton } from '@taiga-ui/core';
import { TuiSegmented } from '@taiga-ui/kit/components/segmented';
import { UiDate, UiSelect } from '../../core/ui';
import { mskDate } from '../backtest/backtest-model';
import { REPLAY_TIMEFRAMES } from '../replay/replay.store';
import {
  type ChartTimeframe,
  describeBar,
  higherTimeframes,
  TIMEFRAMES,
} from './chart-data';
import { barDelta, describeDelta, indicatorValuesAt } from './chart-view';
import { ChartStore } from './chart.store';
import { type DrawingTool, DrawingsStore } from './drawings.store';
import { IndicatorPanel } from './indicator-panel';
import { ProfileBar } from './profile-bar';
import { ProfilesStore } from './profiles.store';
import { IndicatorsStore } from './indicators.store';
import { PriceChart } from './price-chart';
import { StructurePanel } from './structure-panel';
import { describeTrend, TREND_ARROWS } from './trend';
import { levelReference, type Overlay } from './structure';
import { StructureStore } from './structure.store';
import { loadView, persistedFilter, saveView } from './view-state';
import type { Candle } from '@trader/api-client';

/** Где печатают: там горячие клавиши не перехватываются (флажки и кнопки — не в счёт). */
const TEXT_ENTRY =
  'input:not([type=checkbox]):not([type=radio]), textarea, select, [contenteditable]';

const DRAWING_HINTS: Record<DrawingTool, string> = {
  hline: 'Кликните по графику — на этой цене встанет горизонтальная линия.',
  trend: 'Кликните по двум точкам графика — через них пройдёт линия.',
};

/** Chart: свечи и объём инструмента, слои структуры, рисование, подгрузка истории. */
@Component({
  selector: 'app-chart',
  imports: [
    FormsModule,
    IndicatorPanel,
    PriceChart,
    ProfileBar,
    RouterLink,
    StructurePanel,
    TuiButton,
    TuiSegmented,
    UiDate,
    UiSelect,
  ],
  providers: [
    ChartStore,
    DrawingsStore,
    IndicatorsStore,
    ProfilesStore,
    StructureStore,
  ],
  host: { '(window:keydown)': 'onKey($event)' },
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
          <button
            type="button"
            [title]="'Клавиша ' + (timeframes.indexOf(tf) + 1)"
            (click)="store.selectTimeframe(tf)"
          >
            {{ tf }}
          </button>
        }
      </tui-segmented>
      @if (trendBadge(); as badge) {
        <span
          class="trend-badge"
          [class]="badge.info.state"
          title="Тренд на текущем таймфрейме"
        >
          {{ badge.arrow }} {{ badge.text }}
        </span>
      }
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

        <div
          class="toolbar tools"
          role="toolbar"
          aria-label="Инструменты графика"
        >
          <button
            tuiButton
            type="button"
            size="s"
            appearance="secondary"
            title="К последней свече (End)"
            (click)="toLast()"
          >
            К последней
          </button>
          <button
            tuiButton
            type="button"
            size="s"
            appearance="secondary"
            title="Вписать всю историю (F)"
            (click)="fit()"
          >
            Вписать
          </button>
          <label class="toggle-chip" title="Логарифмическая шкала (L)">
            <input
              type="checkbox"
              [ngModel]="logScale()"
              (ngModelChange)="logScale.set($event)"
            />
            Лог. шкала
          </label>
          <span class="divider"></span>
          <button
            tuiButton
            type="button"
            size="s"
            [appearance]="drawings.tool() === 'hline' ? 'primary' : 'secondary'"
            title="Горизонтальная линия (H)"
            (click)="drawings.toggle('hline')"
          >
            Горизонталь
          </button>
          <button
            tuiButton
            type="button"
            size="s"
            [appearance]="drawings.tool() === 'trend' ? 'primary' : 'secondary'"
            title="Трендовая линия (T)"
            (click)="drawings.toggle('trend')"
          >
            Тренд
          </button>
          <span class="divider"></span>
          <button
            tuiButton
            type="button"
            size="s"
            appearance="secondary"
            title="Загрузить свечи от последней загруженной даты до сегодняшнего дня"
            [disabled]="store.catchingUp()"
            (click)="catchUp()"
          >
            Догрузить
          </button>
          @if (store.catchUpMessage(); as message) {
            <span class="muted" role="status">{{ message }}</span>
          }
          <span class="divider"></span>
          <app-date
            label="Перейти к дате"
            aria-label="Перейти к дате"
            [ngModel]="''"
            (ngModelChange)="goToDate($event)"
          />
          @if (replayLink(); as link) {
            <a
              tuiButton
              size="s"
              appearance="secondary"
              routerLink="/replay"
              [queryParams]="link"
              title="Открыть Replay с дня выбранного бара"
            >
              Replay с {{ link.date }}
            </a>
          }
        </div>

        @if (drawings.tool(); as tool) {
          <p class="muted" role="status">
            {{ hints[tool] }}
            @if (tool === 'trend' && drawings.pending()) {
              Первая точка есть — выберите вторую.
            }
            Esc — отмена.
          </p>
        }
        @if (drawings.drawings().length) {
          <div
            class="toolbar lines"
            role="list"
            aria-label="Нарисованные линии"
          >
            @for (line of drawings.drawings(); track line.id) {
              <span class="toggle-chip line" role="listitem">
                {{
                  line.kind === 'hline' ? 'Горизонталь ' + line.price : 'Тренд'
                }}
                <button
                  type="button"
                  class="remove"
                  [attr.aria-label]="'Удалить линию'"
                  (click)="drawings.remove(line.id)"
                >
                  ×
                </button>
              </span>
            }
            <button
              tuiButton
              type="button"
              size="xs"
              appearance="flat"
              (click)="drawings.clear()"
            >
              Убрать все
            </button>
          </div>
        }

        <div class="legend">
          <span class="bar">{{ legend().text }}</span>
          @if (legend().delta; as delta) {
            <span [class.up]="delta.up" [class.down]="!delta.up">{{
              delta.text
            }}</span>
          }
          @for (value of legend().values; track value.title) {
            <span class="value">
              <i [style.background]="value.color"></i>{{ value.title }}
              {{ value.value }}
            </span>
          }
        </div>
        <div class="chart" [class.drawing]="drawings.tool()">
          <app-price-chart
            [candles]="store.candles()"
            [datasetKey]="store.datasetKey()"
            [indicators]="indicators.series()"
            [overlay]="overlay()"
            [logScale]="logScale()"
            [focus]="focus()"
            (pointPicked)="onPoint($event)"
            (levelPicked)="onLevel($event)"
            (needOlder)="store.loadOlder()"
            (hover)="hovered.set($event)"
          />
        </div>
        <p class="muted">
          Время — МСК. Свечи показаны как есть, без склеек и поправок.
          @if (store.hasOlder()) {
            Прокрутите влево — подгрузится более ранняя история.
          }
          Клавиши: 1–5 — таймфрейм, L — лог. шкала, F — вписать, End — к
          последней, H/T — линии, Esc — отмена. Графики:
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
    .tools,
    .lines {
      margin-bottom: 0;
      gap: 0.5rem;
    }
    .divider {
      width: 1px;
      height: 1.5rem;
      background: var(--tui-border-normal);
    }
    .trend-badge {
      padding: 0.2rem 0.7rem;
      border: 1px solid currentColor;
      border-radius: 999px;
      font-size: 0.85rem;
      font-weight: 600;
    }
    .trend-badge.uptrend {
      color: #26a69a;
    }
    .trend-badge.downtrend {
      color: #ef5350;
    }
    .profiles {
      margin-inline-start: auto;
    }
    .chart {
      height: calc(100vh - 20rem);
      min-height: 26rem;
    }
    .chart.drawing {
      cursor: crosshair;
    }
    .legend {
      display: flex;
      flex-wrap: wrap;
      gap: 0.25rem 1rem;
      min-height: 1.25rem;
      font-family: monospace;
      font-size: 0.85rem;
    }
    .up {
      color: #26a69a;
    }
    .down {
      color: #ef5350;
    }
    .value i {
      display: inline-block;
      width: 0.6rem;
      height: 0.6rem;
      margin-inline-end: 0.3rem;
      border-radius: 50%;
    }
    .line .remove {
      border: 0;
      background: none;
      color: inherit;
      font: inherit;
      cursor: pointer;
    }
  `,
})
export class Chart implements OnInit {
  protected readonly store = inject(ChartStore);
  protected readonly indicators = inject(IndicatorsStore);
  protected readonly structure = inject(StructureStore);
  protected readonly drawings = inject(DrawingsStore);
  protected readonly timeframes = TIMEFRAMES;
  protected readonly hints = DRAWING_HINTS;
  protected readonly instrumentOptions = computed(() =>
    this.store.instruments().map((i) => ({ value: i.id, label: i.ticker })),
  );
  protected readonly hovered = signal<Candle | null>(null);
  protected readonly logScale = signal(false);
  /** Бар, по которому кликнули последним: от него можно открыть Replay. */
  private readonly pickedTime = signal<number | null>(null);
  protected readonly focus = signal<{ time: number; seq: number } | null>(null);
  private focusSeq = 0;
  private readonly graph = viewChild(PriceChart);
  private restoredInstrument: number | null = null;

  /** Слои структуры и нарисованные вручную линии — одним оверлеем. */
  protected readonly overlay = computed<Overlay>(() => {
    const base = this.structure.overlay();
    const extra = this.drawings.segments();
    return extra.length
      ? { ...base, segments: [...base.segments, ...extra] }
      : base;
  });

  /** Легенда: бар под курсором (иначе последний), его изменение и значения индикаторов. */
  protected readonly legend = computed(() => {
    const candles = this.store.candles();
    const candle = this.hovered() ?? candles.at(-1);
    if (!candle) {
      return { text: '', delta: null, values: [] };
    }
    const index = candles.findIndex((c) => c.timestamp === candle.timestamp);
    const delta = barDelta(
      candle,
      index > 0 ? candles[index - 1] : undefined,
      this.structure.reference()?.atr ?? null,
    );
    return {
      text: describeBar(candle),
      delta: delta && { up: delta.up, text: describeDelta(delta) },
      values: indicatorValuesAt(this.indicators.series(), candle),
    };
  });

  protected readonly trendBadge = computed(() => {
    const info = this.structure.trends()[0]?.info;
    return info
      ? {
          info,
          arrow: TREND_ARROWS[info.state],
          text: describeTrend(info),
        }
      : null;
  });

  protected readonly replayLink = computed(() => {
    const time = this.pickedTime();
    const instrument = this.store.instrumentId();
    const tf = this.store.timeframe();
    if (
      time === null ||
      instrument === null ||
      !(REPLAY_TIMEFRAMES as readonly string[]).includes(tf)
    ) {
      return null;
    }
    return {
      instrument,
      tf,
      date: mskDate(new Date(time * 1000).toISOString()),
    };
  });

  constructor() {
    // Вид прошлого визита: данные подгрузятся, когда придут свечи.
    const view = loadView();
    this.restoredInstrument = view.instrumentId ?? null;
    if (view.timeframe) {
      this.store.timeframe.set(view.timeframe as ChartTimeframe);
    }
    this.logScale.set(view.logScale ?? false);
    this.structure.restore(view);

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
        const higher = this.structure.higherTimeframe();
        if (
          higher &&
          !(higherTimeframes(timeframe) as string[]).includes(higher)
        ) {
          this.structure.higherTimeframe.set(null);
        }
        this.structure.setReference(levelReference(candles));
        void this.indicators.refresh({
          ...series,
          chartTimeframe: timeframe,
          start: first.timestamp,
        });
        void this.structure.refresh({ ...series, chartTimeframe: timeframe });
        void this.structure.refreshTrend();
      });
    });

    // Нарисованные линии хранятся отдельно по инструментам.
    effect(() => {
      const instrumentId = this.store.instrumentId();
      untracked(() => this.drawings.setInstrument(instrumentId));
    });

    // Запоминаем вид; пока инструмент не выбран, ничего не пишем (не затираем сохранённый).
    effect(() => {
      const instrumentId = this.store.instrumentId();
      const view = {
        instrumentId,
        timeframe: this.store.timeframe(),
        layers: this.structure.layers(),
        levelFilter: persistedFilter(this.structure.levelFilter()),
        lastBars: this.structure.lastBars(),
        higherTimeframe: this.structure.higherTimeframe(),
        logScale: this.logScale(),
      };
      if (instrumentId !== null) {
        saveView(view);
      }
    });
  }

  ngOnInit(): void {
    void this.store.loadInstruments(this.restoredInstrument);
    void this.indicators.loadCatalog();
  }

  protected onProfileTimeframe(timeframe: string): void {
    if ((TIMEFRAMES as readonly string[]).includes(timeframe)) {
      void this.store.selectTimeframe(timeframe as ChartTimeframe);
    }
  }

  /** Догрузка новых свечей; движки и индикаторы пересчитываются на новых барах. */
  protected async catchUp(): Promise<void> {
    if (await this.store.catchUp()) {
      void this.structure.recompute().then(() => this.structure.refreshTrend());
    }
  }

  protected toLast(): void {
    this.graph()?.toLast();
  }

  protected fit(): void {
    this.graph()?.fit();
  }

  /** Клик по графику: рисование, ручная сетка Fibonacci или выбор бара для Replay. */
  protected onPoint(point: { time: number; price: number }): void {
    if (this.drawings.tool()) {
      this.drawings.pick(point);
      return;
    }
    this.pickedTime.set(point.time);
    void this.structure.pickPoint(point);
  }

  protected onLevel(id: number): void {
    if (!this.drawings.tool() && !this.structure.manualMode()) {
      this.structure.selectLevel(id);
    }
  }

  /** Подгружает историю до даты (МСК) и показывает график вокруг неё. */
  protected async goToDate(date: string | null): Promise<void> {
    if (!date) {
      return;
    }
    const start = new Date(`${date}T00:00:00+03:00`);
    if (Number.isNaN(start.getTime())) {
      return;
    }
    await this.store.loadUntil(start.toISOString());
    this.focus.set({
      time: Math.floor(start.getTime() / 1000),
      seq: ++this.focusSeq,
    });
  }

  protected onKey(event: KeyboardEvent): void {
    const target = event.target;
    if (
      event.ctrlKey ||
      event.metaKey ||
      event.altKey ||
      (target instanceof Element && target.closest(TEXT_ENTRY))
    ) {
      return;
    }
    const digit = /^Digit([1-9])$/.exec(event.code)?.[1];
    const timeframe = digit ? TIMEFRAMES[Number(digit) - 1] : undefined;
    if (timeframe) {
      void this.store.selectTimeframe(timeframe);
      return;
    }
    switch (event.code) {
      case 'KeyL':
        this.logScale.update((on) => !on);
        break;
      case 'KeyF':
        this.fit();
        break;
      case 'End':
        this.toLast();
        break;
      case 'KeyH':
        this.drawings.toggle('hline');
        break;
      case 'KeyT':
        this.drawings.toggle('trend');
        break;
      case 'Escape':
        this.drawings.cancel();
        if (this.structure.manualMode()) {
          this.structure.toggleManual();
        }
        break;
      default:
        return;
    }
    event.preventDefault();
  }
}
