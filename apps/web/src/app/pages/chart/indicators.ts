import type { IndicatorInfo, IndicatorValues } from '@trader/api-client';
import { toChartTime, TIMEFRAMES } from './chart-data';

/** Индикатор, добавленный на график. */
export interface ActiveIndicator {
  id: string;
  name: string;
  title: string;
  params: Record<string, unknown>;
  sourceTimeframe: string;
}

/** Поле формы параметров, построенное из JSON-схемы Pydantic. */
export interface ParamField {
  key: string;
  label: string;
  kind: 'integer' | 'number' | 'boolean' | 'string';
  min?: number;
  max?: number;
  step: number | 'any';
  value: unknown;
}

interface SchemaProperty {
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
}

/** Поля формы из `params_schema` индикатора (значения — по умолчанию). */
export function paramFields(
  info: Pick<IndicatorInfo, 'params_schema' | 'defaults'>,
): ParamField[] {
  const properties = (info.params_schema['properties'] ?? {}) as Record<
    string,
    SchemaProperty
  >;
  return Object.entries(properties).map(([key, property]) => {
    const kind =
      property.type === 'integer' ||
      property.type === 'number' ||
      property.type === 'boolean'
        ? property.type
        : 'string';
    return {
      key,
      label: property.description ?? property.title ?? key,
      kind,
      min: property.minimum ?? property.exclusiveMinimum,
      max: property.maximum ?? property.exclusiveMaximum,
      step: kind === 'integer' ? 1 : 'any',
      value: info.defaults[key] ?? property.default ?? '',
    };
  });
}

/** Значения формы → параметры запроса (числа — числами, пустое — по умолчанию). */
export function collectParams(fields: ParamField[]): Record<string, unknown> {
  const params: Record<string, unknown> = {};
  for (const field of fields) {
    if (
      field.value === '' ||
      field.value === null ||
      field.value === undefined
    ) {
      continue;
    }
    params[field.key] =
      field.kind === 'integer' || field.kind === 'number'
        ? Number(field.value)
        : field.value;
  }
  return params;
}

/** Source TF, разрешённые на графике `chartTf` (не младше chart TF). */
export function allowedSourceTimeframes(chartTf: string): string[] {
  const rank = TIMEFRAMES.indexOf(chartTf as (typeof TIMEFRAMES)[number]);
  return TIMEFRAMES.filter((_, index) => index >= rank);
}

export function describeParams(params: Record<string, unknown>): string {
  const values = Object.values(params);
  return values.length ? `(${values.join(', ')})` : '';
}

const PALETTE = [
  '#f59e0b',
  '#3b82f6',
  '#a855f7',
  '#10b981',
  '#ef4444',
  '#ec4899',
];

export interface IndicatorLine {
  name: string;
  kind: 'line' | 'histogram';
  color: string;
  data: { time: number; value: number | null }[];
}

/** Готовая к рисованию серия индикатора для обёртки графика. */
export interface ChartIndicator {
  id: string;
  title: string;
  pane: 'price' | 'separate';
  lines: IndicatorLine[];
}

/** Оттенки выходов одного индикатора (первый — базовый цвет). */
function shade(base: string, index: number): string {
  const opacity = [1, 0.7, 0.5][index] ?? 0.4;
  const r = parseInt(base.slice(1, 3), 16);
  const g = parseInt(base.slice(3, 5), 16);
  const b = parseInt(base.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${opacity})`;
}

export function toChartIndicator(
  active: ActiveIndicator,
  values: IndicatorValues,
  colorIndex: number,
): ChartIndicator {
  const base = PALETTE[colorIndex % PALETTE.length];
  return {
    id: active.id,
    title: `${active.title.split(' — ')[0]}${describeParams(active.params)} · ${active.sourceTimeframe}`,
    pane: values.pane === 'price' ? 'price' : 'separate',
    lines: values.outputs.map((name, index) => ({
      name,
      kind: name === 'histogram' ? 'histogram' : 'line',
      color: shade(base, index),
      data: values.points.map((point) => ({
        time: toChartTime(point.timestamp),
        value: point.valid ? (point.values[name] ?? null) : null,
      })),
    })),
  };
}

export interface WarmupHint {
  id: string;
  text: string;
}

/**
 * Подсказка о прогреве: «нужно N баров, есть M», если значений ещё нет вовсе,
 * либо сколько баров графика ушло на прогрев.
 */
export function warmupHint(
  active: ActiveIndicator,
  values: IndicatorValues,
): WarmupHint | null {
  const label = `${active.title.split(' — ')[0]}${describeParams(active.params)}`;
  const firstValid = values.points.findIndex((p) => p.valid);
  if (values.points.length && firstValid === -1) {
    return {
      id: active.id,
      text: `${label}: прогрев — нужно ${values.warmup_bars} баров ${active.sourceTimeframe}, есть ${values.source_bar_count}`,
    };
  }
  if (firstValid > 0) {
    return {
      id: active.id,
      text: `${label}: первые ${firstValid} баров графика — прогрев (нужно ${values.warmup_bars} баров ${active.sourceTimeframe})`,
    };
  }
  return null;
}
