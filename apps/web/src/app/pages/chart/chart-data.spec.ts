import type { Candle, Roll } from '@trader/api-client';
import {
  describeBar,
  mergeRolls,
  prependCandles,
  rollMarkers,
  toChartData,
  toChartTime,
} from './chart-data';

function candle(timestamp: string, overrides: Partial<Candle> = {}): Candle {
  const start = new Date(timestamp);
  return {
    timestamp,
    close_time: new Date(start.getTime() + 15 * 60_000).toISOString(),
    open: '100.00000000',
    high: '102.00000000',
    low: '99.00000000',
    close: '101.00000000',
    volume: '15.00000000',
    is_partial: false,
    ...overrides,
  };
}

const ROLL: Roll = {
  from_contract_id: 1,
  to_contract_id: 2,
  rolled_at: '2026-03-13T16:05:00Z',
  ratio: '1.005364468287',
  available_at: '2026-03-13T16:05:00Z',
};

describe('toChartData', () => {
  it('переводит время в секунды UTC, а цены и объём — в числа', () => {
    const data = toChartData([candle('2026-09-28T04:00:00Z')]);

    expect(data.candles[0]).toMatchObject({
      time: Date.UTC(2026, 8, 28, 4) / 1000,
      open: 100,
      high: 102,
      low: 99,
      close: 101,
    });
    expect(data.volume[0]).toMatchObject({
      time: data.candles[0].time,
      value: 15,
    });
  });

  it('цвет зависит от направления, неполные бары бледнее', () => {
    const data = toChartData([
      candle('2026-09-28T04:00:00Z'),
      candle('2026-09-28T04:15:00Z', { close: '98' }),
      candle('2026-09-28T04:30:00Z', { is_partial: true }),
    ]);

    const [up, down, partial] = data.candles;
    expect(up.color).not.toBe(down.color);
    expect(partial.color).toContain('rgba');
    expect(partial.color).not.toBe(up.color);
    expect(data.volume[2].color).toBe(partial.color);
  });
});

describe('rollMarkers', () => {
  const candles = [
    candle('2026-03-13T15:45:00Z'),
    candle('2026-03-13T16:00:00Z'), // закрывается в 16:15 > ролла в 16:05
    candle('2026-03-13T16:15:00Z'),
  ];

  it('ставит маркер на первый бар, закрывающийся после ролла', () => {
    const [marker] = rollMarkers([ROLL], candles, { 1: 'NGH6', 2: 'NGJ6' });

    expect(marker.time).toBe(toChartTime('2026-03-13T16:00:00Z'));
    expect(marker.text).toBe('Ролл NGH6→NGJ6 ×1.0054');
  });

  it('роллы вне показанного диапазона пропускаются', () => {
    expect(rollMarkers([ROLL], candles.slice(0, 1), {})).toEqual([]);
  });
});

describe('describeBar', () => {
  it('показывает МСК, цены, контракт, масштаб и неполноту', () => {
    const text = describeBar(
      candle('2026-09-28T04:00:00Z', {
        contract_id: 2,
        price_factor: '1.0054',
        is_partial: true,
      }),
      { 2: 'NGJ6' },
    );

    expect(text).toContain('28.09.2026, 07:00 МСК');
    expect(text).toContain('C 101.00000000');
    expect(text).toContain('NGJ6');
    expect(text).toContain('×1.0054');
    expect(text).toContain('неполный бар');
  });

  it('для обычного контракта масштаб и контракт не показываются', () => {
    const text = describeBar(candle('2026-09-28T04:00:00Z'), {});

    expect(text).not.toContain('×');
    expect(text).not.toContain('неполный');
  });
});

describe('склейка страниц', () => {
  it('ранняя история добавляется слева без повторов', () => {
    const current = [
      candle('2026-09-28T04:15:00Z'),
      candle('2026-09-28T04:30:00Z'),
    ];
    const older = [
      candle('2026-09-28T04:00:00Z'),
      candle('2026-09-28T04:15:00Z'),
    ];

    const merged = prependCandles(older, current);

    expect(merged.map((c) => c.timestamp)).toEqual([
      '2026-09-28T04:00:00Z',
      '2026-09-28T04:15:00Z',
      '2026-09-28T04:30:00Z',
    ]);
  });

  it('роллы объединяются по моменту и сортируются', () => {
    const later = { ...ROLL, rolled_at: '2026-04-10T16:05:00Z' };

    const merged = mergeRolls([later], [ROLL, later]);

    expect(merged.map((r) => r.rolled_at)).toEqual([
      '2026-03-13T16:05:00Z',
      '2026-04-10T16:05:00Z',
    ]);
  });
});
