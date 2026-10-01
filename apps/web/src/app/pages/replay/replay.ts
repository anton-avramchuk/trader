import type { Candle } from '@trader/api-client';

/**
 * Чистая логика Visual replay (без Angular): что видно при курсоре `cursor`.
 *
 * Таймлайн — свечи по возрастанию времени. Свечи хранятся как есть, без склеек и
 * поправок, поэтому видимая часть — просто первые `cursor` свечей; будущие (за
 * курсором) не возвращаются вовсе (ADR-0028).
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

/** Первые `cursor` свечей таймлайна; будущие (за курсором) не возвращаются вовсе. */
export function visibleCandles(timeline: Candle[], cursor: number): Candle[] {
  return timeline.slice(0, Math.max(0, cursor));
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
