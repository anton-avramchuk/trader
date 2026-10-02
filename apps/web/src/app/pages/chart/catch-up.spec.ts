import type { Candle } from '@trader/api-client';
import { catchUpPeriod, mergeCandles } from './catch-up';

const coverage = (...lasts: (string | null)[]) =>
  lasts.map((last, i) => ({
    timeframe: ['15m', '1h', '4h', '1d', '1w'][i] ?? '1w',
    count: 1,
    first: '2026-01-01T00:00:00Z',
    last,
  }));

const bar = (timestamp: string, close: string): Candle => ({
  timestamp,
  close_time: timestamp,
  open: close,
  high: close,
  low: close,
  close,
  volume: '1',
  trading_day: '2026-09-28',
});

describe('catchUpPeriod', () => {
  const now = new Date('2026-10-02T12:00:00Z');

  it('от дня самой ранней последней свечи до завтра (сегодня входит)', () => {
    expect(
      catchUpPeriod(
        coverage('2026-09-30T15:45:00Z', '2026-09-30T16:00:00Z', null),
        now,
      ),
    ).toEqual({ from: '2026-09-30', to: '2026-10-03' });
  });

  it('день считается по Москве, а не по UTC', () => {
    expect(catchUpPeriod(coverage('2026-09-30T22:00:00Z'), now)?.from).toBe(
      '2026-10-01',
    );
  });

  it('без свечей догружать нечего', () => {
    expect(catchUpPeriod([], now)).toBeNull();
    expect(catchUpPeriod(coverage(null), now)).toBeNull();
  });
});

describe('mergeCandles', () => {
  it('заменяет совпавшие по времени, добавляет новые, держит порядок', () => {
    const merged = mergeCandles(
      [bar('2026-09-30T04:00:00Z', '1'), bar('2026-10-01T04:00:00Z', '2')],
      [bar('2026-10-02T04:00:00Z', '4'), bar('2026-10-01T04:00:00Z', '3')],
    );

    expect(merged.map((c) => [c.timestamp, c.close])).toEqual([
      ['2026-09-30T04:00:00Z', '1'],
      ['2026-10-01T04:00:00Z', '3'],
      ['2026-10-02T04:00:00Z', '4'],
    ]);
  });
});
