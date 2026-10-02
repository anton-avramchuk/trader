import type { Candle, Instrument } from '@trader/api-client';
import { mskDate } from '../backtest/backtest-model';

const DAY_MS = 86_400_000;

/** Период догрузки: даты `YYYY-MM-DD`, конец исключительно (как у `candles.load`). */
export interface CatchUpPeriod {
  from: string;
  to: string;
}

/**
 * От дня (МСК) самой ранней «последней» свечи среди таймфреймов — чтобы каждый
 * таймфрейм дотянулся до конца, перекрытие безвредно (upsert) — до завтра
 * (сегодняшний день входит). Нет свечей — `null`: сначала нужна обычная загрузка.
 */
export function catchUpPeriod(
  coverage: Instrument['coverage'],
  now: Date = new Date(),
): CatchUpPeriod | null {
  const lasts = (coverage ?? [])
    .map((c) => c.last)
    .filter((last): last is string => !!last)
    .map((last) => new Date(last).getTime());
  if (!lasts.length) {
    return null;
  }
  const from = mskDate(new Date(Math.min(...lasts)).toISOString());
  const to = mskDate(new Date(now.getTime() + DAY_MS).toISOString());
  return from < to ? { from, to } : null;
}

/** Склейка с более свежими свечами: совпавшие по времени заменяются, новые добавляются. */
export function mergeCandles(current: Candle[], newer: Candle[]): Candle[] {
  const byTime = new Map(current.map((c) => [c.timestamp, c]));
  for (const candle of newer) {
    byTime.set(candle.timestamp, candle);
  }
  return [...byTime.values()].sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
  );
}
