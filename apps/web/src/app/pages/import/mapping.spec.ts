import type { FileMapping } from '@trader/api-client';
import {
  EMPTY_FORM,
  fromMapping,
  splitPreview,
  toColumnRef,
  toMapping,
  validateForm,
} from './mapping';

const FINAM: FileMapping = {
  delimiter: ',',
  has_header: true,
  encoding: 'utf-8-sig',
  skip_rows: 0,
  decimal_separator: '.',
  timezone: 'Europe/Moscow',
  timestamp_is_close: false,
  datetime: { columns: ['<DATE>', '<TIME>'], format: '%Y%m%d %H%M%S' },
  open: '<OPEN>',
  high: '<HIGH>',
  low: '<LOW>',
  close: '<CLOSE>',
  volume: '<VOL>',
  trade_count: null,
};

describe('маппинг файла', () => {
  it('преобразование туда и обратно не меняет маппинг', () => {
    expect(toMapping(fromMapping(FINAM))).toEqual(FINAM);
  });

  it('номера колонок (файл без заголовка) остаются числами', () => {
    const mapping: FileMapping = {
      ...FINAM,
      has_header: false,
      datetime: { columns: [2, 3], format: '%Y%m%d %H%M%S' },
      open: 4,
      high: 5,
      low: 6,
      close: 7,
      volume: 8,
    };

    const form = fromMapping(mapping);

    expect(form.datetimeColumns).toBe('2, 3');
    expect(toMapping(form)).toEqual(mapping);
  });

  it('toColumnRef различает номер и имя', () => {
    expect(toColumnRef(' 7 ')).toBe(7);
    expect(toColumnRef('<VOL>')).toBe('<VOL>');
    expect(toColumnRef('2024')).toBe(2024);
  });

  it('пустая trade_count даёт null, заполненная — колонку', () => {
    const form = { ...fromMapping(FINAM), tradeCount: 'trades' };

    expect(toMapping(form).trade_count).toBe('trades');
    expect(toMapping({ ...form, tradeCount: '  ' }).trade_count).toBeNull();
  });

  it('разделитель-табуляция вводится как \\t', () => {
    const form = { ...fromMapping(FINAM), delimiter: '\\t' };

    expect(toMapping(form).delimiter).toBe('\t');
  });

  it('валидация перечисляет незаполненное', () => {
    expect(validateForm(EMPTY_FORM)).toEqual(
      expect.arrayContaining([
        'Укажите колонку(и) времени',
        'Укажите колонку open',
        'Укажите колонку volume',
      ]),
    );
    expect(validateForm(fromMapping(FINAM))).toEqual([]);
  });

  it('предпросмотр режет строки по разделителю и пропускает skip_rows', () => {
    const lines = ['служебная строка', 'a;b;c', '1;2;3'];

    expect(splitPreview(lines, ';', 1)).toEqual([
      ['a', 'b', 'c'],
      ['1', '2', '3'],
    ]);
    expect(splitPreview(['x\ty'], '\\t')).toEqual([['x', 'y']]);
  });
});
