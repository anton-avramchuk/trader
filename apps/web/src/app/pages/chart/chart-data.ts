import type { Candle } from '@trader/api-client';
import { formatMsk } from '../../core/time/msk';

export const TIMEFRAMES = ['15m', '1h', '4h', '1d', '1w'] as const;
export type ChartTimeframe = (typeof TIMEFRAMES)[number];

const UP = '#26a69a';
const DOWN = '#ef5350';

/** Секунды Unix (UTC) — формат времени Lightweight Charts. */
export function toChartTime(iso: string): number {
  return Math.floor(new Date(iso).getTime() / 1000);
}

export interface CandlePoint {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  color: string;
  wickColor: string;
  borderColor: string;
}

export interface VolumePoint {
  time: number;
  value: number;
  color: string;
}

/** Свечи и объём для графика. */
export function toChartData(candles: Candle[]): {
  candles: CandlePoint[];
  volume: VolumePoint[];
} {
  const points: CandlePoint[] = [];
  const volume: VolumePoint[] = [];
  for (const c of candles) {
    const time = toChartTime(c.timestamp);
    const open = Number(c.open);
    const close = Number(c.close);
    const up = close >= open;
    const color = up ? UP : DOWN;
    points.push({
      time,
      open,
      high: Number(c.high),
      low: Number(c.low),
      close,
      color,
      wickColor: color,
      borderColor: color,
    });
    volume.push({ time, value: Number(c.volume), color });
  }
  return { candles: points, volume };
}

/** Подпись свечи для легенды под курсором. */
export function describeBar(c: Candle): string {
  return [
    formatMsk(c.timestamp, 'datetime') + ' МСК',
    `O ${c.open}`,
    `H ${c.high}`,
    `L ${c.low}`,
    `C ${c.close}`,
    `V ${Number(c.volume)}`,
  ].join(' · ');
}

/** Склейка страниц истории: более ранние бары слева, без повторов по времени. */
export function prependCandles(older: Candle[], current: Candle[]): Candle[] {
  const known = new Set(current.map((c) => c.timestamp));
  return [...older.filter((c) => !known.has(c.timestamp)), ...current];
}
