import { TestBed } from '@angular/core/testing';
import { DrawingsStore, parseDrawings } from './drawings.store';

function setup() {
  TestBed.configureTestingModule({ providers: [DrawingsStore] });
  return TestBed.inject(DrawingsStore);
}

describe('DrawingsStore', () => {
  beforeEach(() => localStorage.clear());

  it('горизонталь ставится одним кликом и сразу выключает инструмент', () => {
    const store = setup();
    store.setInstrument(1);
    store.toggle('hline');

    store.pick({ time: 100, price: 123.456789 });

    expect(store.tool()).toBeNull();
    expect(store.drawings()).toMatchObject([
      { kind: 'hline', price: 123.4568 },
    ]);
    expect(store.segments()[0]).toMatchObject({
      time1: 0,
      time2: null,
      price1: 123.4568,
      label: '123.4568',
    });
  });

  it('трендовая линия — по двум точкам; одинаковое время игнорируется', () => {
    const store = setup();
    store.setInstrument(1);
    store.toggle('trend');

    store.pick({ time: 100, price: 10 });
    expect(store.pending()).toEqual({ time: 100, price: 10 });
    store.pick({ time: 100, price: 20 });
    expect(store.drawings()).toEqual([]);
    store.pick({ time: 200, price: 20 });

    expect(store.drawings()).toMatchObject([
      {
        kind: 'trend',
        from: { time: 100, price: 10 },
        to: { time: 200, price: 20 },
      },
    ]);
    expect(store.pending()).toBeNull();
    expect(store.segments()[0]).toMatchObject({ time1: 100, time2: 200 });
  });

  it('без выбранного инструмента клики ничего не рисуют', () => {
    const store = setup();
    store.setInstrument(1);

    store.pick({ time: 1, price: 1 });

    expect(store.drawings()).toEqual([]);
  });

  it('повторный выбор инструмента выключает его, cancel сбрасывает точку', () => {
    const store = setup();
    store.toggle('trend');
    store.pick({ time: 1, price: 1 });

    store.cancel();
    expect(store.tool()).toBeNull();
    expect(store.pending()).toBeNull();

    store.toggle('hline');
    store.toggle('hline');
    expect(store.tool()).toBeNull();
  });

  it('линии хранятся отдельно по инструментам и переживают перезагрузку', () => {
    const store = setup();
    store.setInstrument(1);
    store.toggle('hline');
    store.pick({ time: 1, price: 5 });

    store.setInstrument(2);
    expect(store.drawings()).toEqual([]);
    store.setInstrument(1);

    expect(store.drawings()).toMatchObject([{ kind: 'hline', price: 5 }]);
  });

  it('удаление одной и всех линий', () => {
    const store = setup();
    store.setInstrument(1);
    for (const price of [1, 2]) {
      store.toggle('hline');
      store.pick({ time: 1, price });
    }
    const [first] = store.drawings();

    store.remove(first?.id as string);
    expect(store.drawings()).toHaveLength(1);
    store.clear();

    expect(store.drawings()).toEqual([]);
    store.setInstrument(1);
    expect(store.drawings()).toEqual([]);
  });

  it('разбор отбрасывает мусор', () => {
    expect(parseDrawings(null)).toEqual([]);
    expect(parseDrawings('{')).toEqual([]);
    expect(parseDrawings('{"a":1}')).toEqual([]);
    expect(
      parseDrawings(
        JSON.stringify([
          { id: 'a', kind: 'hline', price: 1 },
          { id: 'b', kind: 'hline', price: 'x' },
          { kind: 'hline', price: 1 },
          { id: 'c', kind: 'trend', from: { time: 1, price: 1 }, to: null },
          null,
        ]),
      ),
    ).toEqual([{ id: 'a', kind: 'hline', price: 1 }]);
  });
});
