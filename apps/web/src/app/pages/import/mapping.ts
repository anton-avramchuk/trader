import type { FileMapping } from '@trader/api-client';

/** Плоская форма маппинга для редактирования в UI (все поля — строки/флаги). */
export interface MappingForm {
  delimiter: string;
  hasHeader: boolean;
  encoding: string;
  skipRows: number;
  decimalSeparator: '.' | ',';
  timezone: string;
  timestampIsClose: boolean;
  datetimeColumns: string;
  datetimeFormat: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  tradeCount: string;
}

export const EMPTY_FORM: MappingForm = {
  delimiter: ',',
  hasHeader: true,
  encoding: 'utf-8-sig',
  skipRows: 0,
  decimalSeparator: '.',
  timezone: 'Europe/Moscow',
  timestampIsClose: false,
  datetimeColumns: '',
  datetimeFormat: 'iso',
  open: '',
  high: '',
  low: '',
  close: '',
  volume: '',
  tradeCount: '',
};

/** Колонка в маппинге — имя (с заголовком) или номер (без него; строка из цифр). */
export function toColumnRef(text: string): string | number {
  const value = text.trim();
  return /^\d+$/.test(value) ? Number(value) : value;
}

function fromColumnRef(ref: string | number | null | undefined): string {
  return ref === null || ref === undefined ? '' : String(ref);
}

export function fromMapping(mapping: FileMapping): MappingForm {
  return {
    delimiter: mapping.delimiter === '\t' ? '\\t' : (mapping.delimiter ?? ','),
    hasHeader: mapping.has_header ?? true,
    encoding: mapping.encoding ?? 'utf-8-sig',
    skipRows: mapping.skip_rows ?? 0,
    decimalSeparator: mapping.decimal_separator ?? '.',
    timezone: mapping.timezone ?? 'Europe/Moscow',
    timestampIsClose: mapping.timestamp_is_close ?? false,
    datetimeColumns: mapping.datetime.columns.map(String).join(', '),
    datetimeFormat: mapping.datetime.format,
    open: fromColumnRef(mapping.open),
    high: fromColumnRef(mapping.high),
    low: fromColumnRef(mapping.low),
    close: fromColumnRef(mapping.close),
    volume: fromColumnRef(mapping.volume),
    tradeCount: fromColumnRef(mapping.trade_count),
  };
}

export function toMapping(form: MappingForm): FileMapping {
  const columns = form.datetimeColumns
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean)
    .map(toColumnRef);
  return {
    delimiter: form.delimiter === '\\t' ? '\t' : form.delimiter,
    has_header: form.hasHeader,
    encoding: form.encoding.trim(),
    skip_rows: Number(form.skipRows) || 0,
    decimal_separator: form.decimalSeparator,
    timezone: form.timezone.trim(),
    timestamp_is_close: form.timestampIsClose,
    datetime: { columns, format: form.datetimeFormat.trim() },
    open: toColumnRef(form.open),
    high: toColumnRef(form.high),
    low: toColumnRef(form.low),
    close: toColumnRef(form.close),
    volume: toColumnRef(form.volume),
    trade_count: form.tradeCount.trim() ? toColumnRef(form.tradeCount) : null,
  };
}

/** Проверка до отправки: что заполнено не всё, объяснять пользователю понятно. */
export function validateForm(form: MappingForm): string[] {
  const problems: string[] = [];
  if (!form.datetimeColumns.trim()) {
    problems.push('Укажите колонку(и) времени');
  }
  if (!form.datetimeFormat.trim()) {
    problems.push(
      'Укажите формат времени (iso, epoch_s, epoch_ms или strptime)',
    );
  }
  const labels: [string, string][] = [
    [form.open, 'open'],
    [form.high, 'high'],
    [form.low, 'low'],
    [form.close, 'close'],
    [form.volume, 'volume'],
  ];
  for (const [value, label] of labels) {
    if (!value.trim()) {
      problems.push(`Укажите колонку ${label}`);
    }
  }
  if (form.delimiter.length === 0) {
    problems.push('Укажите разделитель');
  }
  return problems;
}

/** Разбор строк предпросмотра по разделителю (простой, без кавычек — только для показа). */
export function splitPreview(
  lines: string[],
  delimiter: string,
  skipRows = 0,
): string[][] {
  const separator = delimiter === '\\t' ? '\t' : delimiter;
  return lines.slice(skipRows).map((line) => line.split(separator));
}
