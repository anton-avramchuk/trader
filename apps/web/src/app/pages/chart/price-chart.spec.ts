import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { Candle, Roll } from '@trader/api-client';
import { PriceChart } from './price-chart';

// jsdom не умеет canvas: подменяем библиотеку и проверяем, что обёртка с ней делает.
const lib = vi.hoisted(() => {
  const candleSeries = { setData: vi.fn() };
  const volumeSeries = { setData: vi.fn() };
  const markers = { setMarkers: vi.fn() };
  const timeScale = {
    subscribeVisibleLogicalRangeChange: vi.fn(),
    fitContent: vi.fn(),
    scrollToRealTime: vi.fn(),
    getVisibleRange: vi.fn(() => ({ from: 1, to: 2 })),
    setVisibleRange: vi.fn(),
  };
  const chart = {
    addSeries: vi.fn((definition: string) =>
      definition === 'candles' ? candleSeries : volumeSeries,
    ),
    priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
    timeScale: vi.fn(() => timeScale),
    subscribeCrosshairMove: vi.fn(),
    remove: vi.fn(),
  };
  return { candleSeries, volumeSeries, markers, timeScale, chart };
});

vi.mock('lightweight-charts', () => ({
  CandlestickSeries: 'candles',
  HistogramSeries: 'volume',
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
    is_partial: false,
  };
}

@Component({
  imports: [PriceChart],
  template: `<app-price-chart
    [candles]="candles"
    [rolls]="rolls()"
    [labels]="labels"
    [datasetKey]="key"
    (needOlder)="older = older + 1"
    (hover)="hovered = $event"
  />`,
})
class Host {
  candles: Candle[] = [
    candle('2026-09-28T04:00:00Z'),
    candle('2026-09-28T04:15:00Z'),
  ];
  rolls = signal<Roll[]>([]);
  labels = { 1: 'A', 2: 'B' };
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

  it('ставит маркеры роллов', async () => {
    const fixture = await mount();
    fixture.componentInstance.rolls.set([
      {
        from_contract_id: 1,
        to_contract_id: 2,
        rolled_at: '2026-09-28T04:05:00Z',
        ratio: '1.01',
        available_at: '2026-09-28T04:05:00Z',
      },
    ]);

    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();

    const markers = lib.markers.setMarkers.mock.calls.at(-1)?.[0] as {
      text: string;
    }[];
    expect(markers).toHaveLength(1);
    expect(markers[0].text).toContain('Ролл A→B');
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

  it('уничтожает график при удалении компонента', async () => {
    const fixture = await mount();

    fixture.destroy();

    expect(lib.chart.remove).toHaveBeenCalled();
  });
});
