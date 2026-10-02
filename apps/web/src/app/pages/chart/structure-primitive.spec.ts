import { spreadLabels } from './structure-primitive';

describe('spreadLabels', () => {
  it('разведённые подписи остаются на месте', () => {
    expect(spreadLabels([10, 50, 100])).toEqual([10, 50, 100]);
  });

  it('близкие подписи раздвигаются вниз, порядок входа сохраняется', () => {
    expect(spreadLabels([100, 102, 101])).toEqual([100, 126, 113]);
  });

  it('пусто — пусто', () => {
    expect(spreadLabels([])).toEqual([]);
  });
});
