import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';
import type { Candle } from '@trader/api-client';
import type { ChartIndicator } from './indicators';
import { EMPTY_OVERLAY, type Overlay } from './structure';
import { PriceChart } from './price-chart';
import { StructurePrimitive } from './structure-primitive';

// jsdom не умеет canvas: подменяем библиотеку и проверяем, что обёртка с ней делает.
const lib = vi.hoisted(() => {
  const candleSeries = {
    setData: vi.fn(),
    attachPrimitive: vi.fn(),
    coordinateToPrice: vi.fn(() => 123.5),
  };
  const volumeSeries = { setData: vi.fn() };
  const markers = { setMarkers: vi.fn() };
  const timeScale = {
    subscribeVisibleLogicalRangeChange: vi.fn(),
    fitContent: vi.fn(),
    scrollToRealTime: vi.fn(),
    getVisibleRange: vi.fn(() => ({ from: 1, to: 2 })),
    setVisibleRange: vi.fn(),
    getVisibleLogicalRange: vi.fn(() => ({ from: 0, to: 20 })),
    setVisibleLogicalRange: vi.fn(),
  };
  const priceScale = { applyOptions: vi.fn() };
  const chart = {
    addSeries: vi.fn((...args: [string, unknown?, number?]) => {
      const definition = args[0];
      if (definition === 'candles') {
        return candleSeries;
      }
      if (definition === 'volume') {
        return volumeSeries;
      }
      return { setData: vi.fn(), definition };
    }),
    priceScale: vi.fn(() => priceScale),
    timeScale: vi.fn(() => timeScale),
    subscribeCrosshairMove: vi.fn(),
    subscribeClick: vi.fn(),
    removeSeries: vi.fn(),
    remove: vi.fn(),
  };
  return { candleSeries, volumeSeries, markers, timeScale, priceScale, chart };
});

vi.mock('lightweight-charts', () => ({
  CandlestickSeries: 'candles',
  HistogramSeries: 'volume',
  LineSeries: 'line',
  createChart: vi.fn(() => lib.chart),
  createSeriesMarkers: vi.fn(() => lib.markers),
}));

function candle(timestamp: string): Candle {
  const start = new Date(timestamp);
  return {
    timestamp,
    close_time: new Date(start.getTime() + 900_000).toISOString(),
    open: '1',
    high: '2',
    low: '1',
    close: '2',
    volume: '5',
    trading_day: '2026-09-28',
  };
}

@Component({
  imports: [PriceChart],
  template: `<app-price-chart
    [candles]="candles"
    [datasetKey]="key"
    [indicators]="indicators()"
    [overlay]="overlay()"
    [logScale]="log()"
    [focus]="focus()"
    (levelPicked)="level = $event"
    (pointPicked)="picked = $event"
    (needOlder)="older = older + 1"
    (hover)="hovered = $event"
  />`,
})
class Host {
  candles: Candle[] = [
    candle('2026-09-28T04:00:00Z'),
    candle('2026-09-28T04:15:00Z'),
  ];
  indicators = signal<ChartIndicator[]>([]);
  overlay = signal<Overlay>(EMPTY_OVERLAY);
  log = signal(false);
  focus = signal<{ time: number; seq: number } | null>(null);
  level: number | undefined;
  picked: { time: number; price: number } | undefined;
  key = 'a';
  older = 0;
  hovered: Candle | null | undefined;
}

describe('PriceChart', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  async function mount() {
    TestBed.configureTestingModule({ imports: [Host] });
    const fixture = TestBed.createComponent(Host);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return fixture;
  }

  it('рисует свечи и объём и подгоняет масштаб при первой загрузке', async () => {
    await mount();

    expect(lib.candleSeries.setData).toHaveBeenCalled();
    const points = lib.candleSeries.setData.mock.calls.at(-1)?.[0] as {
      time: number;
    }[];
    expect(points.map((p) => p.time)).toEqual([
      Date.UTC(2026, 8, 28, 4, 0) / 1000,
      Date.UTC(2026, 8, 28, 4, 15) / 1000,
    ]);
    expect(lib.volumeSeries.setData).toHaveBeenCalled();
    expect(lib.timeScale.fitContent).toHaveBeenCalled();
  });

  it('слои структуры: маркеры и примитив подключены', async () => {
    const fixture = await mount();
    fixture.componentInstance.overlay.set({
      markers: [
        {
          time: Date.UTC(2026, 8, 28, 4, 15) / 1000,
          position: 'aboveBar',
          shape: 'arrowDown',
          color: '#f00',
          text: 'HH',
        },
      ],
      segments: [],
      zones: [],
    });

    fixture.detectChanges();
    await fixture.whenStable();

    expect(lib.candleSeries.attachPrimitive).toHaveBeenCalledTimes(1);
    const markers = lib.markers.setMarkers.mock.calls.at(-1)?.[0] as {
      text: string;
    }[];
    expect(markers.map((m) => m.text)).toEqual(['HH']);
  });

  it('клик по графику сообщает время бара и цену', async () => {
    const fixture = await mount();
    const [listener] = lib.chart.subscribeClick.mock.calls[0] as [
      (param: { time?: number; point?: { x: number; y: number } }) => void,
    ];

    listener({});
    listener({ time: 1000, point: { x: 5, y: 7 } });

    expect(fixture.componentInstance.picked).toEqual({
      time: 1000,
      price: 123.5,
    });
  });

  it('у левого края просит более раннюю историю', async () => {
    const fixture = await mount();
    const [listener] = lib.timeScale.subscribeVisibleLogicalRangeChange.mock
      .calls[0] as [(range: { from: number; to: number } | null) => void];

    listener({ from: 5, to: 100 });
    listener({ from: 500, to: 600 });
    listener(null);

    expect(fixture.componentInstance.older).toBe(1);
  });

  it('сообщает о баре под курсором', async () => {
    const fixture = await mount();
    const [listener] = lib.chart.subscribeCrosshairMove.mock.calls[0] as [
      (param: { time?: number }) => void,
    ];

    listener({ time: Date.UTC(2026, 8, 28, 4, 15) / 1000 });
    expect(fixture.componentInstance.hovered?.timestamp).toBe(
      '2026-09-28T04:15:00Z',
    );
    listener({});
    expect(fixture.componentInstance.hovered).toBeNull();
  });

  it('индикаторы: оверлей на цене, панель для отдельных, удаление серий', async () => {
    const fixture = await mount();
    const line = (id: string, pane: 'price' | 'separate'): ChartIndicator => ({
      id,
      title: id,
      pane,
      lines: [
        {
          name: 'value',
          kind: 'line',
          color: '#fff',
          data: [
            { time: 1, value: null },
            { time: 2, value: 5 },
          ],
        },
      ],
    });

    fixture.componentInstance.indicators.set([
      line('a', 'price'),
      line('b', 'separate'),
    ]);
    fixture.detectChanges();
    await fixture.whenStable();

    const calls = lib.chart.addSeries.mock.calls.filter(([d]) => d === 'line');
    expect(calls.map((c) => c[2])).toEqual([0, 1]); // оверлей — панель 0, отдельный — 1
    const created = lib.chart.addSeries.mock.results
      .filter((_, i) => lib.chart.addSeries.mock.calls[i][0] === 'line')
      .map((r) => r.value as { setData: ReturnType<typeof vi.fn> });
    expect(created[0].setData).toHaveBeenCalledWith([
      { time: 1 },
      { time: 2, value: 5 },
    ]);

    fixture.componentInstance.indicators.set([line('b', 'separate')]);
    fixture.detectChanges();
    await fixture.whenStable();
    // 'b' теперь единственный отдельный (панель 1 — как раньше), 'a' убран
    expect(lib.chart.removeSeries).toHaveBeenCalledTimes(1);
  });

  it('логарифмическая шкала переключается на лету', async () => {
    const fixture = await mount();

    fixture.componentInstance.log.set(true);
    fixture.detectChanges();
    await fixture.whenStable();
    expect(lib.priceScale.applyOptions).toHaveBeenLastCalledWith({ mode: 1 });

    fixture.componentInstance.log.set(false);
    fixture.detectChanges();
    await fixture.whenStable();
    expect(lib.priceScale.applyOptions).toHaveBeenLastCalledWith({ mode: 0 });
  });

  it('вписать и к последней свече', async () => {
    const fixture = await mount();
    const chart = fixture.debugElement.query(By.directive(PriceChart))
      .componentInstance as PriceChart;
    lib.timeScale.fitContent.mockClear();

    chart.fit();
    chart.toLast();

    expect(lib.timeScale.fitContent).toHaveBeenCalledTimes(1);
    expect(lib.timeScale.scrollToRealTime).toHaveBeenCalled();
  });

  it('переход к моменту центрирует окно на ближайшем баре, повтор того же запроса игнорируется', async () => {
    const fixture = await mount();

    fixture.componentInstance.focus.set({
      time: Date.UTC(2026, 8, 28, 4, 15) / 1000,
      seq: 1,
    });
    fixture.detectChanges();
    await fixture.whenStable();

    expect(lib.timeScale.setVisibleLogicalRange).toHaveBeenCalledTimes(1);
    expect(lib.timeScale.setVisibleLogicalRange).toHaveBeenCalledWith({
      from: -9, // бар №1, окно 20 баров сохранено
      to: 11,
    });

    fixture.componentInstance.focus.set({
      time: Date.UTC(2026, 8, 28, 4, 15) / 1000,
      seq: 1,
    });
    fixture.detectChanges();
    await fixture.whenStable();
    expect(lib.timeScale.setVisibleLogicalRange).toHaveBeenCalledTimes(1);
  });

  it('клик по линии уровня сообщает его id', async () => {
    const fixture = await mount();
    const [listener] = lib.chart.subscribeClick.mock.calls[0] as [
      (param: { time?: number; point?: { x: number; y: number } }) => void,
    ];
    const hit = vi.spyOn(StructurePrimitive.prototype, 'levelAt');

    hit.mockReturnValue(7);
    listener({ time: 1000, point: { x: 5, y: 7 } });
    expect(fixture.componentInstance.level).toBe(7);

    fixture.componentInstance.level = undefined;
    hit.mockReturnValue(null);
    listener({ time: 1000, point: { x: 5, y: 7 } });
    expect(fixture.componentInstance.level).toBeUndefined();
    hit.mockRestore();
  });

  it('уничтожает график при удалении компонента', async () => {
    const fixture = await mount();

    fixture.destroy();

    expect(lib.chart.remove).toHaveBeenCalled();
  });
});
