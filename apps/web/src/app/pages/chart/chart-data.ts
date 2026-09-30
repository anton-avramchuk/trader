import type { Candle, Roll } from '@trader/api-client';
import { formatMsk } from '../../core/time/msk';

export const TIMEFRAMES = ['15m', '1h', '4h', '1d', '1w'] as const;
export type ChartTimeframe = (typeof TIMEFRAMES)[number];

const UP = '#26a69a';
const DOWN = '#ef5350';
const PARTIAL_UP = 'rgba(38, 166, 154, 0.35)';
const PARTIAL_DOWN = 'rgba(239, 83, 80, 0.35)';

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

/** Свечи и объём для графика; неполные бары (обрезаны сессией/клирингом) бледнее. */
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
    const color = c.is_partial
      ? up
        ? PARTIAL_UP
        : PARTIAL_DOWN
      : up
        ? UP
        : DOWN;
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

export interface RollMarker {
  time: number;
  position: 'aboveBar';
  shape: 'arrowDown';
  color: string;
  text: string;
}

/**
 * Маркеры роллов: на первом баре нового контракта. `labels` — id контракта → SECID.
 * Ролл вне показанного диапазона пропускается.
 */
export function rollMarkers(
  rolls: Roll[],
  candles: Candle[],
  labels: Record<number, string>,
): RollMarker[] {
  const markers: RollMarker[] = [];
  for (const roll of rolls) {
    const rolledAt = Date.parse(roll.rolled_at);
    const first = candles.find((c) => Date.parse(c.close_time) > rolledAt);
    if (!first) {
      continue;
    }
    const from = labels[roll.from_contract_id] ?? roll.from_contract_id;
    const to = labels[roll.to_contract_id] ?? roll.to_contract_id;
    markers.push({
      time: toChartTime(first.timestamp),
      position: 'aboveBar',
      shape: 'arrowDown',
      color: '#f5a623',
      text: `Ролл ${from}→${to} ×${Number(roll.ratio).toFixed(4)}`,
    });
  }
  return markers.sort((a, b) => a.time - b.time);
}

/** Подпись бара для легенды под курсором. */
export function describeBar(c: Candle, labels: Record<number, string>): string {
  const parts = [
    formatMsk(c.timestamp, 'datetime') + ' МСК',
    `O ${c.open}`,
    `H ${c.high}`,
    `L ${c.low}`,
    `C ${c.close}`,
    `V ${Number(c.volume)}`,
  ];
  if (c.contract_id != null) {
    parts.push(labels[c.contract_id] ?? `контракт ${c.contract_id}`);
  }
  if (c.price_factor != null && Number(c.price_factor) !== 1) {
    parts.push(`×${Number(c.price_factor).toFixed(4)}`);
  }
  if (c.is_partial) {
    parts.push('неполный бар');
  }
  return parts.join(' · ');
}

/** Склейка страниц истории: более ранние бары слева, без повторов по времени. */
export function prependCandles(older: Candle[], current: Candle[]): Candle[] {
  const known = new Set(current.map((c) => c.timestamp));
  return [...older.filter((c) => !known.has(c.timestamp)), ...current];
}

export function mergeRolls(current: Roll[], incoming: Roll[]): Roll[] {
  const byMoment = new Map(current.map((r) => [r.rolled_at, r]));
  for (const roll of incoming) {
    byMoment.set(roll.rolled_at, roll);
  }
  return [...byMoment.values()].sort((a, b) =>
    a.rolled_at.localeCompare(b.rolled_at),
  );
}
