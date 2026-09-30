import { Pipe, PipeTransform } from '@angular/core';

export const MSK_TIMEZONE = 'Europe/Moscow';

export type MskFormat = 'datetime' | 'date' | 'time' | 'full';

const OPTIONS: Record<MskFormat, Intl.DateTimeFormatOptions> = {
  datetime: {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  },
  full: {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  },
  date: { year: 'numeric', month: '2-digit', day: '2-digit' },
  time: { hour: '2-digit', minute: '2-digit' },
};

/** Время для показа пользователю — всегда по Москве (хранится и передаётся в UTC). */
export function formatMsk(
  value: string | number | Date | null | undefined,
  format: MskFormat = 'datetime',
): string {
  if (value === null || value === undefined || value === '') {
    return '';
  }
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) {
    return '';
  }
  return new Intl.DateTimeFormat('ru-RU', {
    ...OPTIONS[format],
    timeZone: MSK_TIMEZONE,
    hourCycle: 'h23',
  }).format(date);
}

@Pipe({ name: 'msk' })
export class MskPipe implements PipeTransform {
  transform(
    value: string | number | Date | null | undefined,
    format: MskFormat = 'datetime',
  ): string {
    return formatMsk(value, format);
  }
}
