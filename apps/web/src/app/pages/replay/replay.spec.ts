import type { Candle } from '@trader/api-client';
import {
  clampCursor,
  compareBars,
  knownAt,
  mskDate,
  mskMidnightUtc,
  visibleCandles,
} from './replay';

function bar(hour: number, close: number): Candle {
  const start = Date.UTC(2026, 2, 12, hour);
  return {
    timestamp: new Date(start).toISOString(),
    close_time: new Date(start + 3600_000).toISOString(),
    open: String(close),
    high: String(close + 1),
    low: String(close - 1),
    close: String(close),
    volume: '10',
    trading_day: '2026-03-12',
  };
}

const TIMELINE = [
  bar(9, 200),
  bar(10, 202),
  bar(11, 204),
  bar(12, 100),
  bar(13, 101),
];

describe('время МСК', () => {
  it('полночь по Москве — это 21:00 UTC предыдущих суток', () => {
    expect(mskMidnightUtc('2026-09-28')).toBe('2026-09-27T21:00:00.000Z');
  });

  it('дата по Москве и обратно', () => {
    expect(mskDate('2026-09-27T21:30:00Z')).toBe('2026-09-28');
    expect(mskDate(mskMidnightUtc('2026-01-01'))).toBe('2026-01-01');
  });
});

describe('visibleCandles', () => {
  it('будущие бары не возвращаются', () => {
    expect(visibleCandles(TIMELINE, 2)).toHaveLength(2);
    expect(visibleCandles(TIMELINE, 0)).toEqual([]);
    expect(visibleCandles(TIMELINE, 99)).toHaveLength(5);
  });

  it('свечи показываются как есть, без поправок', () => {
    expect(visibleCandles(TIMELINE, 3).map((c) => Number(c.close))).toEqual([
      200, 202, 204,
    ]);
  });

  it('исходный таймлайн не меняется', () => {
    const copy = structuredClone(TIMELINE);

    visibleCandles(TIMELINE, 2);

    expect(TIMELINE).toEqual(copy);
  });
});

describe('момент знания', () => {
  it('это закрытие последнего видимого бара', () => {
    expect(knownAt(TIMELINE, 0)).toBeNull();
    expect(knownAt(TIMELINE, 2)).toBe(TIMELINE[1].close_time);
  });

  it('курсор ограничивается длиной таймлайна', () => {
    expect(clampCursor(-3, 5)).toBe(0);
    expect(clampCursor(9, 5)).toBe(5);
    expect(clampCursor(2, 5)).toBe(2);
  });
});

describe('compareBars', () => {
  it('совпадающие бары дают ноль расхождений', () => {
    const shown = visibleCandles(TIMELINE, 3);

    expect(compareBars(shown, shown)).toEqual({ checked: 3, mismatches: 0 });
  });

  it('считает расхождения и отсутствующие бары', () => {
    const shown = visibleCandles(TIMELINE, 3);
    const server = [{ ...shown[0], close: '999' }, shown[1], bar(20, 1)];

    expect(compareBars(shown, server)).toEqual({ checked: 3, mismatches: 2 });
  });
});
