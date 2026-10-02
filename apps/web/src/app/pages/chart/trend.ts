import type { EngineEvent } from '@trader/api-client';

export type TrendState = 'uptrend' | 'downtrend' | 'range';
export type TrendBucket = 'weak' | 'medium' | 'strong' | 'none';

/** Текущая картина тренда на одном таймфрейме (последнее событие `trend`). */
export interface TrendInfo {
  state: TrendState;
  strength: number;
  bucket: TrendBucket;
  since: string;
  efficiency: number;
}

export interface TrendRow {
  timeframe: string;
  info: TrendInfo | null;
}

export const TREND_TITLES: Record<TrendState, string> = {
  uptrend: 'Восходящий',
  downtrend: 'Нисходящий',
  range: 'Боковик',
};
export const TREND_ARROWS: Record<TrendState, string> = {
  uptrend: '↑',
  downtrend: '↓',
  range: '↔',
};
const BUCKET_TITLES: Record<TrendBucket, string> = {
  weak: 'слабый',
  medium: 'средний',
  strong: 'сильный',
  none: '',
};

export function trendInfo(events: EngineEvent[]): TrendInfo | null {
  const last = events.filter((e) => e.kind === 'trend').at(-1);
  if (!last) {
    return null;
  }
  const p = last.payload as Record<string, unknown>;
  return {
    state: String(p['state']) as TrendState,
    strength: Number(p['strength']),
    bucket: String(p['bucket']) as TrendBucket,
    since: String(p['since']),
    efficiency: Number(p['efficiency']),
  };
}

/** «Восходящий, сильный» / «Боковик». */
export function describeTrend(info: TrendInfo): string {
  const bucket = BUCKET_TITLES[info.bucket];
  return bucket
    ? `${TREND_TITLES[info.state]}, ${bucket}`
    : TREND_TITLES[info.state];
}
