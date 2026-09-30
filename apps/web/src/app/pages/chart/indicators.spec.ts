import type { IndicatorValues } from '@trader/api-client';
import {
  type ActiveIndicator,
  allowedSourceTimeframes,
  collectParams,
  describeParams,
  paramFields,
  toChartIndicator,
  warmupHint,
} from './indicators';

const MACD_INFO = {
  params_schema: {
    properties: {
      fast: {
        type: 'integer',
        minimum: 1,
        maximum: 5000,
        description: 'Быстрая EMA',
      },
      slow: { type: 'integer', minimum: 2, description: 'Медленная EMA' },
      mult: { type: 'number', exclusiveMinimum: 0, title: 'Mult' },
      flag: { type: 'boolean' },
      label: { type: 'string' },
    },
  },
  defaults: { fast: 12, slow: 26, mult: 2, flag: true, label: 'x' },
};

function active(overrides: Partial<ActiveIndicator> = {}): ActiveIndicator {
  return {
    id: 'ind-1',
    name: 'ema',
    title: 'EMA — экспоненциальная скользящая средняя',
    params: { period: 200 },
    sourceTimeframe: '1h',
    ...overrides,
  };
}

function values(overrides: Partial<IndicatorValues> = {}): IndicatorValues {
  const point = (minute: number, valid: boolean, value: number | null) => ({
    timestamp: new Date(Date.UTC(2026, 8, 28, 4, minute)).toISOString(),
    values: { value },
    valid,
    source_timestamp: null,
    available_at: null,
  });
  return {
    indicator: 'ema',
    version: 1,
    params: { period: 200 },
    params_hash: 'h',
    pane: 'price',
    outputs: ['value'],
    chart_timeframe: '15m',
    source_timeframe: '1h',
    warmup_bars: 200,
    source_bar_count: 50,
    source_truncated: false,
    as_of: null,
    points: [point(0, false, null), point(15, true, 3.5), point(30, true, 3.6)],
    ...overrides,
  };
}

describe('paramFields / collectParams', () => {
  it('строит поля из JSON-схемы: типы, границы, подписи, значения по умолчанию', () => {
    const fields = paramFields(MACD_INFO);

    expect(fields.map((f) => [f.key, f.kind])).toEqual([
      ['fast', 'integer'],
      ['slow', 'integer'],
      ['mult', 'number'],
      ['flag', 'boolean'],
      ['label', 'string'],
    ]);
    expect(fields[0]).toMatchObject({
      label: 'Быстрая EMA',
      min: 1,
      max: 5000,
      step: 1,
      value: 12,
    });
    expect(fields[2]).toMatchObject({
      label: 'Mult',
      min: 0,
      step: 'any',
      value: 2,
    });
    expect(fields[3].value).toBe(true);
  });

  it('значения формы превращаются в параметры: числа — числами, пустое пропускается', () => {
    const fields = paramFields(MACD_INFO);
    fields[0].value = '9'; // ввод пользователя приходит строкой
    fields[1].value = '';
    fields[4].value = 'abc';

    expect(collectParams(fields)).toEqual({
      fast: 9,
      mult: 2,
      flag: true,
      label: 'abc',
    });
  });

  it('схема без параметров даёт пустую форму', () => {
    expect(
      paramFields({ params_schema: { type: 'object' }, defaults: {} }),
    ).toEqual([]);
  });
});

describe('source TF', () => {
  it('на графике разрешены только TF не младше chart TF', () => {
    expect(allowedSourceTimeframes('15m')).toEqual([
      '15m',
      '1h',
      '4h',
      '1d',
      '1w',
    ]);
    expect(allowedSourceTimeframes('4h')).toEqual(['4h', '1d', '1w']);
    expect(allowedSourceTimeframes('1w')).toEqual(['1w']);
  });
});

describe('toChartIndicator', () => {
  it('оверлей: значения до прогрева — пропуски, время в секундах UTC', () => {
    const chart = toChartIndicator(active(), values(), 0);

    expect(chart.pane).toBe('price');
    expect(chart.title).toBe('EMA(200) · 1h');
    expect(chart.lines).toHaveLength(1);
    expect(chart.lines[0].data.map((p) => p.value)).toEqual([null, 3.5, 3.6]);
    expect(chart.lines[0].data[0].time).toBe(
      Date.UTC(2026, 8, 28, 4, 0) / 1000,
    );
  });

  it('несколько выходов: гистограмма MACD и разные оттенки, отдельная панель', () => {
    const macd = values({
      pane: 'separate',
      outputs: ['macd', 'signal', 'histogram'],
      points: [
        {
          timestamp: '2026-09-28T04:00:00Z',
          values: { macd: 1, signal: 0.5, histogram: 0.5 },
          valid: true,
          source_timestamp: null,
          available_at: null,
        },
      ],
    });

    const chart = toChartIndicator(
      active({ name: 'macd', title: 'MACD', params: {} }),
      macd,
      1,
    );

    expect(chart.pane).toBe('separate');
    expect(chart.lines.map((l) => [l.name, l.kind])).toEqual([
      ['macd', 'line'],
      ['signal', 'line'],
      ['histogram', 'histogram'],
    ]);
    expect(new Set(chart.lines.map((l) => l.color)).size).toBe(3);
  });
});

describe('warmupHint', () => {
  it('нет ни одного валидного значения: «нужно N баров, есть M»', () => {
    const hint = warmupHint(
      active(),
      values({ points: values().points.map((p) => ({ ...p, valid: false })) }),
    );

    expect(hint?.text).toContain('нужно 200 баров 1h, есть 50');
  });

  it('первые бары графика — прогрев', () => {
    expect(warmupHint(active(), values())?.text).toContain(
      'первые 1 баров графика',
    );
  });

  it('без прогрева и без данных подсказки нет', () => {
    expect(
      warmupHint(active(), values({ points: values().points.slice(1) })),
    ).toBeNull();
    expect(warmupHint(active(), values({ points: [] }))).toBeNull();
  });
});

describe('describeParams', () => {
  it('перечисляет значения или молчит', () => {
    expect(describeParams({ fast: 12, slow: 26 })).toBe('(12, 26)');
    expect(describeParams({})).toBe('');
  });
});
