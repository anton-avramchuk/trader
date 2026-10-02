import { computed, Injectable, signal } from '@angular/core';
import type { OverlaySegment } from './structure';

export interface DrawingPoint {
  time: number;
  price: number;
}

/** Нарисованное вручную: горизонтальная или трендовая линия. */
export type Drawing =
  | { id: string; kind: 'hline'; price: number }
  | { id: string; kind: 'trend'; from: DrawingPoint; to: DrawingPoint };

export type DrawingTool = 'hline' | 'trend';

const KEY = 'trader.chart.drawings.v1';
export const DRAWING_COLOR = '#00acc1';

const finite = (value: unknown): value is number =>
  typeof value === 'number' && Number.isFinite(value);

const isPoint = (value: unknown): value is DrawingPoint =>
  typeof value === 'object' &&
  value !== null &&
  finite((value as DrawingPoint).time) &&
  finite((value as DrawingPoint).price);

/** Разбор сохранённого списка: всё, что не похоже на линию, отбрасывается. */
export function parseDrawings(raw: string | null): Drawing[] {
  let data: unknown;
  try {
    data = raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
  if (!Array.isArray(data)) {
    return [];
  }
  return data.flatMap((item: Record<string, unknown>): Drawing[] => {
    const id = typeof item?.['id'] === 'string' ? item['id'] : null;
    if (!id) {
      return [];
    }
    if (item['kind'] === 'hline' && finite(item['price'])) {
      return [{ id, kind: 'hline', price: item['price'] }];
    }
    if (
      item['kind'] === 'trend' &&
      isPoint(item['from']) &&
      isPoint(item['to'])
    ) {
      return [{ id, kind: 'trend', from: item['from'], to: item['to'] }];
    }
    return [];
  });
}

/** Линии на графике: рисуются кликами, хранятся в браузере отдельно по инструментам. */
@Injectable()
export class DrawingsStore {
  readonly tool = signal<DrawingTool | null>(null);
  readonly pending = signal<DrawingPoint | null>(null);
  readonly drawings = signal<Drawing[]>([]);
  private instrumentId: number | null = null;

  readonly segments = computed<OverlaySegment[]>(() => {
    return this.drawings().map((d): OverlaySegment => {
      if (d.kind === 'hline') {
        return {
          time1: 0,
          price1: d.price,
          time2: null,
          price2: d.price,
          color: DRAWING_COLOR,
          dashed: false,
          width: 1,
          label: String(d.price),
        };
      }
      return {
        time1: d.from.time,
        price1: d.from.price,
        time2: d.to.time,
        price2: d.to.price,
        color: DRAWING_COLOR,
        dashed: false,
        width: 2,
      };
    });
  });

  /** Подгружает линии выбранного инструмента. */
  setInstrument(instrumentId: number | null): void {
    this.instrumentId = instrumentId;
    this.cancel();
    this.drawings.set(this.read());
  }

  /** Включает инструмент (повторный выбор выключает). */
  toggle(tool: DrawingTool): void {
    this.pending.set(null);
    this.tool.update((current) => (current === tool ? null : tool));
  }

  cancel(): void {
    this.tool.set(null);
    this.pending.set(null);
  }

  /** Клик по графику: горизонталь ставится сразу, трендовая — по двум кликам. */
  pick(raw: DrawingPoint): void {
    const tool = this.tool();
    if (!tool) {
      return;
    }
    const point = { time: raw.time, price: Number(raw.price.toFixed(4)) };
    if (tool === 'hline') {
      this.add({ id: newId(), kind: 'hline', price: point.price });
      this.cancel();
      return;
    }
    const first = this.pending();
    if (!first) {
      this.pending.set(point);
      return;
    }
    if (first.time === point.time) {
      return; // вертикальная линия не имеет смысла: ждём другой бар
    }
    this.add({ id: newId(), kind: 'trend', from: first, to: point });
    this.cancel();
  }

  remove(id: string): void {
    this.update(this.drawings().filter((d) => d.id !== id));
  }

  clear(): void {
    this.update([]);
  }

  private add(drawing: Drawing): void {
    this.update([...this.drawings(), drawing]);
  }

  private update(drawings: Drawing[]): void {
    this.drawings.set(drawings);
    try {
      localStorage.setItem(this.key(), JSON.stringify(drawings));
    } catch {
      // хранилище недоступно: линии живут до перезагрузки страницы
    }
  }

  private read(): Drawing[] {
    try {
      return parseDrawings(localStorage.getItem(this.key()));
    } catch {
      return [];
    }
  }

  private key(): string {
    return `${KEY}.${this.instrumentId}`;
  }
}

let counter = 0;
const newId = (): string => `${Date.now().toString(36)}-${counter++}`;
