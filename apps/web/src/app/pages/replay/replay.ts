import type { Candle, Roll } from '@trader/api-client';

/**
 * Чистая логика Visual replay (без Angular): что видно при курсоре `cursor`.
 *
 * Таймлайн — бары по возрастанию времени в текущем масштабе continuous. Чтобы
 * показать серию «как её видели на момент t», цены делятся на ratio роллов,
 * которые к моменту `t` ещё не были известны (`available_at > t`) и лежат после
 * бара. Так масштаб совпадает с серверным `snapshot?as_of=t` (ADR-0019).
 */

const MSK_OFFSET_MS = 3 * 3600_000;

/** Полночь по Москве заданной даты (`ГГГГ-ММ-ДД`) как ISO UTC. */
export function mskMidnightUtc(date: string): string {
  const [year, month, day] = date.split('-').map(Number);
  return new Date(Date.UTC(year, month - 1, day) - MSK_OFFSET_MS).toISOString();
}

/** Дата (`ГГГГ-ММ-ДД`) по Москве для момента UTC. */
export function mskDate(iso: string): string {
  return new Date(Date.parse(iso) + MSK_OFFSET_MS).toISOString().slice(0, 10);
}

/** Момент знания: закрытие последнего видимого бара (null, если ничего не видно). */
export function knownAt(timeline: Candle[], cursor: number): string | null {
  return cursor > 0 ? (timeline[cursor - 1]?.close_time ?? null) : null;
}

function scaled(candle: Candle, divisor: number): Candle {
  if (divisor === 1) {
    return candle;
  }
  const fix = (value: string) => String(Number(value) / divisor);
  return {
    ...candle,
    open: fix(candle.open),
    high: fix(candle.high),
    low: fix(candle.low),
    close: fix(candle.close),
  };
}

/**
 * Первые `cursor` баров таймлайна в масштабе на момент знания. Будущие бары
 * (за курсором) не возвращаются вовсе.
 */
export function visibleCandles(
  timeline: Candle[],
  rolls: Roll[],
  cursor: number,
): Candle[] {
  const shown = timeline.slice(0, Math.max(0, cursor));
  const moment = knownAt(timeline, cursor);
  if (moment === null) {
    return [];
  }
  const now = Date.parse(moment);
  const unknown = rolls.filter((roll) => Date.parse(roll.available_at) > now);
  if (!unknown.length) {
    return shown;
  }
  return shown.map((candle) => {
    const closed = Date.parse(candle.close_time);
    const divisor = unknown
      .filter((roll) => Date.parse(roll.rolled_at) >= closed)
      .reduce((product, roll) => product * Number(roll.ratio), 1);
    return scaled(candle, divisor);
  });
}

/** Роллы, известные на момент курсора (для показа «что система знала»). */
export function knownRolls(
  timeline: Candle[],
  rolls: Roll[],
  cursor: number,
): Roll[] {
  const moment = knownAt(timeline, cursor);
  if (moment === null) {
    return [];
  }
  const now = Date.parse(moment);
  return rolls.filter((roll) => Date.parse(roll.available_at) <= now);
}

export function clampCursor(cursor: number, length: number): number {
  return Math.min(Math.max(cursor, 0), length);
}

/** Совпадение баров клиента и сервера с относительной точностью `tolerance`. */
export function compareBars(
  shown: Candle[],
  server: Candle[],
  tolerance = 1e-9,
): { checked: number; mismatches: number } {
  let mismatches = 0;
  const byTime = new Map(shown.map((c) => [c.timestamp, c]));
  for (const bar of server) {
    const mine = byTime.get(bar.timestamp);
    const same =
      mine !== undefined &&
      (['open', 'high', 'low', 'close'] as const).every((field) => {
        const a = Number(mine[field]);
        const b = Number(bar[field]);
        return Math.abs(a - b) <= tolerance * Math.max(1, Math.abs(b));
      });
    if (!same) {
      mismatches++;
    }
  }
  return { checked: server.length, mismatches };
}
