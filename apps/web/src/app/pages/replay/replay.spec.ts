import type { Candle, Roll } from '@trader/api-client';
import {
  clampCursor,
  compareBars,
  knownAt,
  knownRolls,
  mskDate,
  mskMidnightUtc,
  visibleCandles,
} from './replay';

function bar(hour: number, close: number, contract = 1): Candle {
  const start = Date.UTC(2026, 2, 12, hour);
  return {
    timestamp: new Date(start).toISOString(),
    close_time: new Date(start + 3600_000).toISOString(),
    open: String(close),
    high: String(close + 1),
    low: String(close - 1),
    close: String(close),
    volume: '10',
    is_partial: false,
    contract_id: contract,
  };
}

// Ролл в 2026-03-12T12:00Z: бары до него — контракт 1 (уже умножены на ratio 2).
const ROLL: Roll = {
  from_contract_id: 1,
  to_contract_id: 2,
  rolled_at: '2026-03-12T12:00:00Z',
  ratio: '2',
  available_at: '2026-03-12T12:00:00Z',
};
const TIMELINE = [
  bar(9, 200),
  bar(10, 202),
  bar(11, 204),
  bar(12, 100, 2),
  bar(13, 101, 2),
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
    expect(visibleCandles(TIMELINE, [], 2)).toHaveLength(2);
    expect(visibleCandles(TIMELINE, [], 0)).toEqual([]);
  });

  it('пока ролл неизвестен, старые бары показаны в их собственном масштабе', () => {
    const shown = visibleCandles(TIMELINE, [ROLL], 2);

    expect(shown.map((c) => Number(c.close))).toEqual([100, 101]);
    expect(Number(shown[0].open)).toBe(100);
  });

  it('после того как ролл стал известен, масштаб текущего контракта', () => {
    const shown = visibleCandles(TIMELINE, [ROLL], 5);

    expect(shown.map((c) => Number(c.close))).toEqual([
      200, 202, 204, 100, 101,
    ]);
  });

  it('ролл известен ровно с момента available_at', () => {
    // курсор 3: последний бар закрылся в 12:00 == available_at — ролл известен
    expect(
      visibleCandles(TIMELINE, [ROLL], 3).map((c) => Number(c.close)),
    ).toEqual([200, 202, 204]);
    // курсор 2: закрытие 11:00 < available_at — ещё нет
    expect(
      visibleCandles(TIMELINE, [ROLL], 2).map((c) => Number(c.close)),
    ).toEqual([100, 101]);
  });

  it('исходный таймлайн не меняется', () => {
    const copy = structuredClone(TIMELINE);

    visibleCandles(TIMELINE, [ROLL], 2);

    expect(TIMELINE).toEqual(copy);
  });
});

describe('момент знания', () => {
  it('это закрытие последнего видимого бара', () => {
    expect(knownAt(TIMELINE, 0)).toBeNull();
    expect(knownAt(TIMELINE, 2)).toBe(TIMELINE[1].close_time);
  });

  it('роллы известны по available_at', () => {
    expect(knownRolls(TIMELINE, [ROLL], 2)).toEqual([]);
    expect(knownRolls(TIMELINE, [ROLL], 3)).toEqual([ROLL]);
  });

  it('курсор ограничивается длиной таймлайна', () => {
    expect(clampCursor(-3, 5)).toBe(0);
    expect(clampCursor(9, 5)).toBe(5);
    expect(clampCursor(2, 5)).toBe(2);
  });
});

describe('compareBars', () => {
  it('совпадающие бары дают ноль расхождений', () => {
    const shown = visibleCandles(TIMELINE, [ROLL], 3);

    expect(compareBars(shown, shown)).toEqual({ checked: 3, mismatches: 0 });
  });

  it('считает расхождения и отсутствующие бары', () => {
    const shown = visibleCandles(TIMELINE, [ROLL], 3);
    const server = [{ ...shown[0], close: '999' }, shown[1], bar(20, 1)];

    expect(compareBars(shown, server)).toEqual({ checked: 3, mismatches: 2 });
  });
});
