import type { Candle } from '@trader/api-client';
import { toChartTime } from './chart-data';
import { barDelta, describeDelta, indicatorValuesAt } from './chart-view';
import type { ChartIndicator } from './indicators';

const bar = (timestamp: string, close: number): Candle => ({
  timestamp,
  close_time: timestamp,
  open: String(close),
  high: String(close),
  low: String(close),
  close: String(close),
  volume: '1',
  trading_day: '2026-09-28',
});

describe('barDelta', () => {
  it('считает изменение к предыдущему закрытию в процентах и ATR', () => {
    const delta = barDelta(
      bar('2026-09-28T05:00:00Z', 102),
      bar('2026-09-28T04:00:00Z', 100),
      4,
    );

    expect(delta).toEqual({ pct: 2, atr: 0.5, up: true });
    expect(describeDelta(delta as never)).toBe('+2.00% · +0.50 ATR');
  });

  it('падение — со знаком минус; без ATR показывает только проценты', () => {
    const delta = barDelta(
      bar('2026-09-28T05:00:00Z', 99),
      bar('2026-09-28T04:00:00Z', 100),
      null,
    );

    expect(delta?.up).toBe(false);
    expect(describeDelta(delta as never)).toBe('−1.00%');
  });

  it('у первого бара изменения нет', () => {
    expect(barDelta(bar('2026-09-28T05:00:00Z', 1), undefined, 1)).toBeNull();
  });
});

describe('indicatorValuesAt', () => {
  const t = (iso: string) => toChartTime(iso);
  const indicators: ChartIndicator[] = [
    {
      id: 'rsi',
      title: 'RSI(14)',
      pane: 'separate',
      lines: [
        {
          name: 'value',
          kind: 'line',
          color: '#f00',
          data: [
            { time: t('2026-09-28T04:00:00Z'), value: null },
            { time: t('2026-09-28T05:00:00Z'), value: 55.123456 },
          ],
        },
      ],
    },
    {
      id: 'macd',
      title: 'MACD',
      pane: 'separate',
      lines: [
        {
          name: 'signal',
          kind: 'line',
          color: '#0f0',
          data: [{ time: t('2026-09-28T05:00:00Z'), value: -1.5 }],
        },
      ],
    },
  ];

  it('значения линий на баре; пустые точки и чужое время пропускаются', () => {
    expect(
      indicatorValuesAt(indicators, bar('2026-09-28T05:00:00Z', 1)),
    ).toEqual([
      { title: 'RSI(14)', value: '55.1235', color: '#f00' },
      { title: 'MACD signal', value: '-1.5', color: '#0f0' },
    ]);
    expect(
      indicatorValuesAt(indicators, bar('2026-09-28T04:00:00Z', 1)),
    ).toEqual([]);
    expect(
      indicatorValuesAt(indicators, bar('2026-09-29T04:00:00Z', 1)),
    ).toEqual([]);
  });
});
