import type { Candle } from '@trader/api-client';
import { toChartTime } from './chart-data';
import type { ChartIndicator } from './indicators';

/** Изменение бара к закрытию предыдущего: в процентах и в ATR графика. */
export interface BarDelta {
  pct: number;
  atr: number | null;
  up: boolean;
}

export function barDelta(
  candle: Candle,
  previous: Candle | undefined,
  atr: number | null,
): BarDelta | null {
  const before = previous ? Number(previous.close) : 0;
  if (!previous || !(before > 0)) {
    return null;
  }
  const change = Number(candle.close) - before;
  return {
    pct: (change / before) * 100,
    atr: atr && atr > 0 ? change / atr : null,
    up: change >= 0,
  };
}

const signed = (value: number, digits: number): string =>
  `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(digits)}`;

export function describeDelta(delta: BarDelta): string {
  const parts = [`${signed(delta.pct, 2)}%`];
  if (delta.atr !== null) {
    parts.push(`${signed(delta.atr, 2)} ATR`);
  }
  return parts.join(' · ');
}

export interface IndicatorValue {
  title: string;
  value: string;
  color: string;
}

/** Значение точки на момент `time` (точное совпадение; данные упорядочены по времени). */
function valueAt(
  data: readonly { time: number; value: number | null }[],
  time: number,
): number | null {
  let lo = 0;
  let hi = data.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const point = data[mid];
    if (!point) {
      return null;
    }
    if (point.time === time) {
      return point.value;
    }
    if (point.time < time) {
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return null;
}

const format = (value: number): string => String(Number(value.toFixed(4)));

/** Значения всех линий индикаторов на баре `candle` (пустые точки пропускаются). */
export function indicatorValuesAt(
  indicators: readonly ChartIndicator[],
  candle: Candle,
): IndicatorValue[] {
  const time = toChartTime(candle.timestamp);
  return indicators.flatMap((indicator) =>
    indicator.lines.flatMap((line) => {
      const value = valueAt(line.data, time);
      return value === null
        ? []
        : [
            {
              title:
                line.name === 'value'
                  ? indicator.title
                  : `${indicator.title} ${line.name}`,
              value: format(value),
              color: line.color,
            },
          ];
    }),
  );
}
