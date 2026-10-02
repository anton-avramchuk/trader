import { DEFAULT_LEVEL_FILTER } from './structure';
import {
  type ChartView,
  loadView,
  parseView,
  persistedFilter,
  saveView,
} from './view-state';

const VIEW: ChartView = {
  instrumentId: 7,
  timeframe: '4h',
  layers: { levels: true, zones: false },
  levelFilter: persistedFilter({ ...DEFAULT_LEVEL_FILTER, perSide: 2 }),
  lastBars: 500,
  higherTimeframe: '1d',
  logScale: true,
};

describe('вид графика', () => {
  beforeEach(() => localStorage.clear());

  it('сохраняется и читается обратно', () => {
    saveView(VIEW);

    expect(loadView()).toEqual(VIEW);
  });

  it('без сохранённого вида — пусто', () => {
    expect(loadView()).toEqual({});
  });

  it('повреждённые данные не ломают страницу', () => {
    expect(parseView('не json')).toEqual({});
    expect(parseView('[1,2]')).toEqual({});
    expect(
      parseView(
        JSON.stringify({
          instrumentId: 'x',
          timeframe: '7m',
          layers: { levels: 1, swings: true, bogus: true },
          lastBars: -5,
          higherTimeframe: 'zz',
          logScale: 'yes',
        }),
      ),
    ).toEqual({ layers: { swings: true } });
  });

  it('фильтр уровней: недостающие поля берутся по умолчанию, null расстояния сохраняется', () => {
    const view = parseView(
      JSON.stringify({ levelFilter: { maxDistanceAtr: null, perSide: 9 } }),
    );

    expect(view.levelFilter).toEqual({
      ...persistedFilter(DEFAULT_LEVEL_FILTER),
      maxDistanceAtr: null,
      perSide: 9,
    });
  });

  it('недоступное хранилище не вызывает ошибок', () => {
    const spy = vi
      .spyOn(Storage.prototype, 'setItem')
      .mockImplementation(() => {
        throw new Error('quota');
      });

    expect(() => saveView(VIEW)).not.toThrow();
    spy.mockRestore();
  });
});
