import { formatMsk, MskPipe } from './msk';

describe('formatMsk', () => {
  it('переводит UTC в московское время', () => {
    expect(formatMsk('2026-09-28T04:00:00Z', 'time')).toBe('07:00');
    expect(formatMsk('2026-09-28T04:00:00Z')).toBe('28.09.2026, 07:00');
  });

  it('переходит на следующие сутки по Москве', () => {
    expect(formatMsk('2026-09-28T21:30:00Z', 'date')).toBe('29.09.2026');
  });

  it('показывает секунды в full независимо от пояса браузера', () => {
    expect(formatMsk(new Date(Date.UTC(2026, 0, 1, 0, 0, 5)), 'full')).toBe(
      '01.01.2026, 03:00:05',
    );
  });

  it('пустые и некорректные значения дают пустую строку', () => {
    expect(formatMsk(null)).toBe('');
    expect(formatMsk(undefined)).toBe('');
    expect(formatMsk('')).toBe('');
    expect(formatMsk('не дата')).toBe('');
  });

  it('пайп делает то же', () => {
    expect(new MskPipe().transform('2026-09-28T04:00:00Z', 'time')).toBe(
      '07:00',
    );
  });
});
