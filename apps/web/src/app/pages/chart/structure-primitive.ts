import type { CanvasRenderingTarget2D } from 'fancy-canvas';
import type {
  IChartApi,
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesApi,
  ISeriesPrimitive,
  SeriesAttachedParameter,
  Time,
  UTCTimestamp,
} from 'lightweight-charts';
import { EMPTY_OVERLAY, type Overlay, snapToBar } from './structure';

const FONT = '11px sans-serif';
const LABEL_GAP = 4;

/** Рисует зоны и линии структуры поверх свечей (primitive Lightweight Charts v5). */
export class StructurePrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private series: ISeriesApi<'Candlestick'> | null = null;
  private redraw: (() => void) | null = null;
  private overlay: Overlay = EMPTY_OVERLAY;
  private times: readonly number[] = [];

  private readonly view: IPrimitivePaneView = {
    zOrder: () => 'normal',
    renderer: (): IPrimitivePaneRenderer => ({
      draw: (target) => this.draw(target),
    }),
  };

  attached(param: SeriesAttachedParameter<Time>): void {
    this.chart = param.chart as IChartApi;
    this.series = param.series as ISeriesApi<'Candlestick'>;
    this.redraw = param.requestUpdate;
  }

  detached(): void {
    this.chart = null;
    this.series = null;
    this.redraw = null;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.view];
  }

  /** Новые данные слоя; `times` — времена баров графика по возрастанию. */
  set(overlay: Overlay, times: readonly number[]): void {
    this.overlay = overlay;
    this.times = times;
    this.redraw?.();
  }

  /** X в пикселях: события привязываются к ближайшему бару не позже их времени. */
  private x(time: number): number | null {
    const chart = this.chart;
    if (!chart) {
      return null;
    }
    const snapped = snapToBar(this.times, time);
    if (snapped === null) {
      return time < (this.times[0] ?? 0) ? 0 : null;
    }
    return chart.timeScale().timeToCoordinate(snapped as UTCTimestamp);
  }

  private draw(target: CanvasRenderingTarget2D): void {
    const series = this.series;
    if (!series) {
      return;
    }
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      ctx.font = FONT;
      for (const zone of this.overlay.zones) {
        const x = this.x(zone.time1);
        const top = series.priceToCoordinate(zone.high);
        const bottom = series.priceToCoordinate(zone.low);
        if (x === null || top === null || bottom === null) {
          continue;
        }
        const height = Math.max(bottom - top, 2);
        ctx.fillStyle = zone.color;
        ctx.fillRect(x, top, mediaSize.width - x, height);
        ctx.fillStyle = '#607d8b';
        ctx.fillText(zone.label, x + LABEL_GAP, top + 11);
      }
      for (const segment of this.overlay.segments) {
        const x1 = this.x(segment.time1);
        const y1 = series.priceToCoordinate(segment.price1);
        const y2 = series.priceToCoordinate(segment.price2);
        const x2 =
          segment.time2 === null ? mediaSize.width : this.x(segment.time2);
        if (x1 === null || x2 === null || y1 === null || y2 === null) {
          continue;
        }
        ctx.beginPath();
        ctx.setLineDash(segment.dashed ? [5, 4] : []);
        ctx.strokeStyle = segment.color;
        ctx.lineWidth = segment.width;
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
        ctx.stroke();
        if (segment.label && segment.time2 === null) {
          ctx.fillStyle = segment.color;
          const width = ctx.measureText(segment.label).width;
          ctx.fillText(segment.label, x2 - width - LABEL_GAP, y1 - LABEL_GAP);
        }
      }
      ctx.setLineDash([]);
    });
  }
}
