import {
  afterNextRender,
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  effect,
  ElementRef,
  inject,
  input,
  output,
  untracked,
  viewChild,
} from '@angular/core';
import type { Candle } from '@trader/api-client';
import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  type IChartApi,
  LineSeries,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts';
import { formatMsk } from '../../core/time/msk';
import { toChartData, toChartTime } from './chart-data';
import type { ChartIndicator } from './indicators';
import { EMPTY_OVERLAY, type Overlay } from './structure';
import { StructurePrimitive } from './structure-primitive';

/** Порог (в барах) у левого края, с которого просим более раннюю историю. */
const LOAD_MORE_THRESHOLD = 30;

/** Полуширина окна (в барах), если текущего видимого диапазона ещё нет. */
const FOCUS_HALF_WIDTH = 60;

const asTime = (value: number) => value as UTCTimestamp;

/**
 * Тонкая обёртка над TradingView Lightweight Charts v5: свечи + объём, маркеры
 * роллов, подгрузка истории у левого края, время в МСК. Данные хранит
 * родитель, обёртка только рисует.
 */
@Component({
  selector: 'app-price-chart',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: '<div #host class="host"></div>',
  styles: `
    :host {
      display: block;
    }
    .host {
      width: 100%;
      height: 100%;
      min-height: 26rem;
    }
  `,
})
export class PriceChart {
  readonly candles = input<Candle[]>([]);
  /** Идентификатор набора данных (инструмент+TF): при смене график начинается заново. */
  readonly datasetKey = input('');
  /** Держать правый край на последнем баре (режим replay). */
  readonly follow = input(false);
  /** Индикаторы: оверлеи на цене и отдельные панели. */
  readonly indicators = input<ChartIndicator[]>([]);
  /** Слои структуры: маркеры, линии и зоны. */
  readonly overlay = input<Overlay>(EMPTY_OVERLAY);
  /** Логарифмическая шкала цены. */
  readonly logScale = input(false);
  /** Запрос «показать момент»: `seq` отличает повторный запрос на то же время. */
  readonly focus = input<{ time: number; seq: number } | null>(null);

  /** Пользователь докрутил до левого края: нужна более ранняя история. */
  readonly needOlder = output<void>();
  /** Бар под курсором (null — курсор ушёл с графика). */
  readonly hover = output<Candle | null>();
  /** Клик по графику: время бара (с) и цена под курсором (ручная сетка Fibonacci). */
  readonly pointPicked = output<{ time: number; price: number }>();
  /** Клик по линии уровня (`levelId` сегмента). */
  readonly levelPicked = output<number>();

  private readonly host = viewChild.required<ElementRef<HTMLElement>>('host');

  private chart: IChartApi | null = null;
  private candleSeries: ISeriesApi<'Candlestick'> | null = null;
  private volumeSeries: ISeriesApi<'Histogram'> | null = null;
  private markers: ISeriesMarkersPluginApi<Time> | null = null;
  private readonly structure = new StructurePrimitive();
  private lastFirstTime: number | null = null;
  private lastKey: string | null = null;
  private lastFocusSeq = 0;
  private readonly indicatorSeries = new Map<
    string,
    ISeriesApi<'Line'> | ISeriesApi<'Histogram'>
  >();

  constructor() {
    afterNextRender(() => this.create());
    inject(DestroyRef).onDestroy(() => this.chart?.remove());
    effect(() => {
      this.render(this.candles());
    });
    effect(() => {
      this.syncIndicators(this.indicators());
    });
    effect(() => {
      this.syncOverlay(this.candles(), this.overlay());
    });
    effect(() => {
      // читаем сигнал до проверки графика, иначе эффект не подпишется на него
      const mode = this.logScale() ? 1 : 0;
      this.chart?.priceScale('right').applyOptions({ mode });
    });
    // после `render`: свечи уже на графике, когда нужно показать момент
    effect(() => {
      const request = this.focus();
      const candles = this.candles();
      untracked(() => this.applyFocus(request, candles));
    });
  }

  /** Вписать всю загруженную историю. */
  fit(): void {
    this.chart?.timeScale().fitContent();
  }

  /** К последней свече. */
  toLast(): void {
    this.chart?.timeScale().scrollToRealTime();
  }

  /** Центрирует график на баре, ближайшем к `time` (ширина окна сохраняется). */
  private applyFocus(
    request: { time: number; seq: number } | null,
    candles: Candle[],
  ): void {
    if (!this.chart || !request || request.seq === this.lastFocusSeq) {
      return;
    }
    this.lastFocusSeq = request.seq;
    const index = candles.findIndex(
      (c) => toChartTime(c.timestamp) >= request.time,
    );
    const target = index < 0 ? candles.length - 1 : index;
    const scale = this.chart.timeScale();
    const range = scale.getVisibleLogicalRange();
    const half = range ? (range.to - range.from) / 2 : FOCUS_HALF_WIDTH;
    scale.setVisibleLogicalRange({ from: target - half, to: target + half });
  }

  private create(): void {
    const chart = createChart(this.host().nativeElement, {
      autoSize: true,
      layout: { attributionLogo: true },
      localization: {
        timeFormatter: (time: Time) =>
          formatMsk((time as number) * 1000, 'datetime'),
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time: Time, type: number) =>
          formatMsk((time as number) * 1000, type >= 3 ? 'time' : 'date'),
      },
      rightPriceScale: {
        scaleMargins: { top: 0.05, bottom: 0.25 },
        mode: this.logScale() ? 1 : 0,
      },
    });
    this.chart = chart;
    this.candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#26a69a',
      downColor: '#ef5350',
      borderVisible: false,
    });
    this.volumeSeries = chart.addSeries(HistogramSeries, {
      priceScaleId: 'volume',
      priceFormat: { type: 'volume' },
    });
    chart
      .priceScale('volume')
      .applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    this.markers = createSeriesMarkers(this.candleSeries, []);
    this.candleSeries.attachPrimitive(this.structure);

    chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (range && range.from < LOAD_MORE_THRESHOLD) {
        this.needOlder.emit();
      }
    });
    chart.subscribeCrosshairMove((param) => {
      const time = param.time as number | undefined;
      const found = time
        ? this.candles().find((c) => toChartTime(c.timestamp) === time)
        : undefined;
      this.hover.emit(found ?? null);
    });
    chart.subscribeClick((param) => {
      const series = this.candleSeries;
      if (!series || !param.point) {
        return;
      }
      const level = this.structure.levelAt(param.point.y);
      if (level !== null) {
        this.levelPicked.emit(level);
      }
      if (param.time === undefined) {
        return;
      }
      const price = series.coordinateToPrice(param.point.y);
      if (price !== null) {
        this.pointPicked.emit({ time: param.time as number, price });
      }
    });
    this.render(this.candles());
    this.syncIndicators(this.indicators());
    this.syncOverlay(this.candles(), this.overlay());
  }

  /** Маркеры и линии/зоны слоёв структуры; свечи не перерисовываются. */
  private syncOverlay(candles: Candle[], overlay: Overlay): void {
    if (!this.chart) {
      return;
    }
    this.markers?.setMarkers(
      [...overlay.markers]
        .sort((a, b) => a.time - b.time)
        .map((m) => ({ ...m, time: asTime(m.time) })),
    );
    this.structure.set(
      overlay,
      candles.map((c) => toChartTime(c.timestamp)),
    );
  }

  /** Приводит серии индикаторов на графике к заданному набору. */
  private syncIndicators(indicators: ChartIndicator[]): void {
    const chart = this.chart;
    if (!chart) {
      return;
    }
    const wanted = new Set<string>();
    let separate = 0;
    for (const indicator of indicators) {
      const pane = indicator.pane === 'price' ? 0 : ++separate;
      for (const line of indicator.lines) {
        // Панель входит в ключ: при смене порядка панелей серия пересоздаётся.
        const key = `${indicator.id}:${line.name}:${pane}`;
        wanted.add(key);
        let series = this.indicatorSeries.get(key);
        if (!series) {
          series =
            line.kind === 'histogram'
              ? chart.addSeries(
                  HistogramSeries,
                  {
                    color: line.color,
                    priceLineVisible: false,
                    lastValueVisible: false,
                  },
                  pane,
                )
              : chart.addSeries(
                  LineSeries,
                  {
                    color: line.color,
                    lineWidth: 2,
                    priceLineVisible: false,
                    lastValueVisible: false,
                    title:
                      line.name === 'value'
                        ? indicator.title
                        : `${indicator.title} ${line.name}`,
                  },
                  pane,
                );
          this.indicatorSeries.set(key, series);
        }
        series.setData(
          line.data.map((p) =>
            p.value === null
              ? { time: asTime(p.time) }
              : { time: asTime(p.time), value: p.value },
          ),
        );
      }
    }
    for (const [key, series] of this.indicatorSeries) {
      if (!wanted.has(key)) {
        chart.removeSeries(series);
        this.indicatorSeries.delete(key);
      }
    }
  }

  private render(candles: Candle[]): void {
    if (!this.chart || !this.candleSeries || !this.volumeSeries) {
      return;
    }
    const key = this.datasetKey();
    if (key !== this.lastKey) {
      this.lastKey = key;
      this.lastFirstTime = null;
    }
    const data = toChartData(candles);
    const first = data.candles[0]?.time ?? null;
    // Если добавилась история слева, сохраняем видимый диапазон по времени.
    const prepended =
      this.lastFirstTime !== null &&
      first !== null &&
      first < this.lastFirstTime;
    const visible = prepended ? this.chart.timeScale().getVisibleRange() : null;

    this.candleSeries.setData(
      data.candles.map((p) => ({ ...p, time: asTime(p.time) })),
    );
    this.volumeSeries.setData(
      data.volume.map((p) => ({ ...p, time: asTime(p.time) })),
    );
    this.syncOverlay(candles, this.overlay());
    if (visible) {
      this.chart.timeScale().setVisibleRange(visible);
    } else if (this.lastFirstTime === null && first !== null) {
      this.chart.timeScale().fitContent();
    }
    if (this.follow() && first !== null) {
      this.chart.timeScale().scrollToRealTime();
    }
    this.lastFirstTime = first;
  }
}
