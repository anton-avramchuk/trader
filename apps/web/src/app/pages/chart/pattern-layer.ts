import type { EngineEvent } from '@trader/api-client';
import { toChartTime } from './chart-data';
import {
  DOWN,
  GOLD,
  NEUTRAL,
  type OverlayMarker,
  type OverlaySegment,
  UP,
} from './structure';

/** Слой «Паттерны» (ADR-0022): вхождения из событий движков паттернов. */

export const PATTERN_TITLES: Record<string, string> = {
  head_shoulders: 'Голова и плечи',
  inverse_head_shoulders: 'Перевёрнутые голова и плечи',
  double_top: 'Двойная вершина',
  double_bottom: 'Двойное дно',
  triple_top: 'Тройная вершина',
  triple_bottom: 'Тройное дно',
  triangle_ascending: 'Восходящий треугольник',
  triangle_descending: 'Нисходящий треугольник',
  triangle_symmetric: 'Симметричный треугольник',
  wedge_rising: 'Восходящий клин',
  wedge_falling: 'Нисходящий клин',
  channel_ascending: 'Восходящий канал',
  channel_descending: 'Нисходящий канал',
  channel_horizontal: 'Горизонтальный канал',
  range_breakout: 'Выход из диапазона',
};

const PATTERN_SHORT: Record<string, string> = {
  head_shoulders: 'H&S',
  inverse_head_shoulders: 'iH&S',
  double_top: '2Top',
  double_bottom: '2Bot',
  triple_top: '3Top',
  triple_bottom: '3Bot',
  triangle_ascending: 'Tri↗',
  triangle_descending: 'Tri↘',
  triangle_symmetric: 'Tri◇',
  wedge_rising: 'Wedge↑',
  wedge_falling: 'Wedge↓',
  channel_ascending: 'Ch↑',
  channel_descending: 'Ch↓',
  channel_horizontal: 'Ch→',
  range_breakout: 'Range',
};

export const shortName = (pattern: string): string =>
  PATTERN_SHORT[pattern] ?? pattern;

export type PatternState = 'candidate' | 'confirmed' | 'invalidated';

export interface PatternPoint {
  role: string;
  price: number;
  ts: string;
  index: number;
}

interface PatternLine {
  p1: number;
  p2: number;
  t1: string | null;
  t2: string | null;
}

/** Вхождение паттерна: последнее состояние цепочки событий. */
export interface PatternInfo {
  key: string;
  engine: string;
  id: number;
  pattern: string;
  direction: 'bullish' | 'bearish';
  state: PatternState;
  reason: string | null;
  points: PatternPoint[];
  line: PatternLine;
  target: number | null;
  height: number;
  detectedAt: string;
  confirmedAt: string | null;
  end: string;
  score: number;
  components: Record<string, number>;
  features: Record<string, number>;
}

/** Вхождения по истории событий движка паттернов (актуальное состояние каждой цепочки). */
export function patternInfos(
  engine: string,
  events: EngineEvent[],
): PatternInfo[] {
  const chains = new Map<number, EngineEvent[]>();
  for (const event of [...events].sort((a, b) => a.seq - b.seq)) {
    if (event.kind !== 'pattern') {
      continue;
    }
    const id = Number(event.payload['id']);
    chains.set(id, [...(chains.get(id) ?? []), event]);
  }
  return [...chains.entries()].map(([id, chain]) => {
    const first = chain[0] as EngineEvent;
    const last = chain[chain.length - 1] as EngineEvent;
    const p = last.payload as Record<string, unknown>;
    const quality = p['quality'] as {
      score: number;
      components: Record<string, number>;
    };
    const target = p['target'];
    return {
      key: `${engine}:${id}`,
      engine,
      id,
      pattern: String(p['pattern']),
      direction: p['direction'] === 'bullish' ? 'bullish' : 'bearish',
      state: String(p['state']) as PatternState,
      reason: p['reason'] === undefined ? null : String(p['reason']),
      points: p['points'] as PatternPoint[],
      line: p['line'] as PatternLine,
      target: target === null || target === undefined ? null : Number(target),
      height: Number(p['height']),
      detectedAt: first.available_at,
      confirmedAt:
        chain.find((e) => e.status === 'confirmed')?.confirmed_at ?? null,
      end: String(p['end']),
      score: quality.score,
      components: quality.components,
      features: p['features'] as Record<string, number>,
    };
  });
}

/** Сколько последних вхождений рисовать: остальные тонут в шуме. */
export const PATTERNS_ON_CHART = 6;

/** Вхождения для графика: новые первыми, отменённые — только по просьбе, выбранное всегда. */
export function chartPatterns(
  all: PatternInfo[],
  showCancelled: boolean,
  selectedKey: string | null = null,
  limit = PATTERNS_ON_CHART,
): PatternInfo[] {
  const visible = all
    .filter((info) => showCancelled || info.state !== 'invalidated')
    .sort((a, b) => Date.parse(b.end) - Date.parse(a.end))
    .slice(0, limit);
  const chosen = all.find((info) => info.key === selectedKey);
  return chosen && !visible.includes(chosen) ? [chosen, ...visible] : visible;
}

export function patternColor(info: PatternInfo): string {
  if (info.state === 'invalidated') {
    return NEUTRAL;
  }
  return info.direction === 'bullish' ? UP : DOWN;
}

/** Контур по точкам, граница (шея) и цель; кандидат — пунктиром, отменённый — серым. */
export function patternSegments(
  infos: PatternInfo[],
  selectedKey: string | null = null,
): OverlaySegment[] {
  const segments: OverlaySegment[] = [];
  for (const info of infos) {
    const color = patternColor(info);
    const width = info.key === selectedKey ? 4 : 2;
    info.points.forEach((point, i) => {
      const next = info.points[i + 1];
      if (next) {
        segments.push({
          time1: toChartTime(point.ts),
          price1: point.price,
          time2: toChartTime(next.ts),
          price2: next.price,
          color,
          dashed: info.state !== 'confirmed',
          width,
        });
      }
    });
    const first = info.points[0];
    const last = info.points[info.points.length - 1];
    if (first && last) {
      segments.push({
        time1: toChartTime(info.line.t1 ?? first.ts),
        price1: info.line.p1,
        time2: toChartTime(info.line.t2 ?? last.ts),
        price2: info.line.p2,
        color: info.state === 'invalidated' ? NEUTRAL : GOLD,
        dashed: false,
        width,
      });
    }
    if (info.target !== null && info.confirmedAt) {
      segments.push({
        time1: toChartTime(info.confirmedAt),
        price1: info.target,
        time2: null,
        price2: info.target,
        color,
        dashed: true,
        width: 1,
        label: `цель ${shortName(info.pattern)}`,
      });
    }
  }
  return segments;
}

/** Метка на последней точке паттерна: короткое имя, «?» у кандидата. */
export function patternMarkers(infos: PatternInfo[]): OverlayMarker[] {
  return infos.flatMap((info) => {
    const last = info.points[info.points.length - 1];
    if (!last) {
      return [];
    }
    const bearish = info.direction === 'bearish';
    return [
      {
        time: toChartTime(last.ts),
        position: bearish ? ('aboveBar' as const) : ('belowBar' as const),
        shape: bearish ? ('arrowDown' as const) : ('arrowUp' as const),
        color: patternColor(info),
        text: `${shortName(info.pattern)}${info.state === 'candidate' ? '?' : ''}`,
      },
    ];
  });
}
