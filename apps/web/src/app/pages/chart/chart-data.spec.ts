import type { Candle } from '@trader/api-client';
import {
  describeBar,
  higherTimeframes,
  prependCandles,
  toChartData,
} from './chart-data';

function candle(timestamp: string, overrides: Partial<Candle> = {}): Candle {
  const start = new Date(timestamp);
  return {
    timestamp,
    close_time: new Date(start.getTime() + 15 * 60_000).toISOString(),
    open: '100.00000000',
    high: '102.00000000',
    low: '99.00000000',
    close: '101.00000000',
    volume: '15.00000000',
    trading_day: '2026-09-28',
    ...overrides,
  };
}

describe('toChartData', () => {
  it('переводит время в секунды UTC, а цены и объём — в числа', () => {
    const data = toChartData([candle('2026-09-28T04:00:00Z')]);

    expect(data.candles[0]).toMatchObject({
      time: Date.UTC(2026, 8, 28, 4) / 1000,
      open: 100,
      high: 102,
      low: 99,
      close: 101,
    });
    expect(data.volume[0]).toMatchObject({
      time: data.candles[0].time,
      value: 15,
    });
  });

  it('цвет зависит от направления', () => {
    const data = toChartData([
      candle('2026-09-28T04:00:00Z'),
      candle('2026-09-28T04:15:00Z', { close: '98' }),
    ]);

    const [up, down] = data.candles;
    expect(up.color).not.toBe(down.color);
    expect(data.volume[1].color).toBe(down.color);
  });
});

describe('describeBar', () => {
  it('показывает МСК и цены', () => {
    const text = describeBar(candle('2026-09-28T04:00:00Z'));

    expect(text).toContain('28.09.2026, 07:00 МСК');
    expect(text).toContain('C 101 ·');
  });
});

describe('склейка страниц', () => {
  it('ранняя история добавляется слева без повторов', () => {
    const current = [
      candle('2026-09-28T04:15:00Z'),
      candle('2026-09-28T04:30:00Z'),
    ];
    const older = [
      candle('2026-09-28T04:00:00Z'),
      candle('2026-09-28T04:15:00Z'),
    ];

    const merged = prependCandles(older, current);

    expect(merged.map((c) => c.timestamp)).toEqual([
      '2026-09-28T04:00:00Z',
      '2026-09-28T04:15:00Z',
      '2026-09-28T04:30:00Z',
    ]);
  });
});

describe('higherTimeframes', () => {
  it('таймфреймы строго старше заданного', () => {
    expect(higherTimeframes('1h')).toEqual(['4h', '1d', '1w']);
    expect(higherTimeframes('1w')).toEqual([]);
    expect(higherTimeframes('3m')).toEqual([]);
  });
});
