import { Component, signal } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideTaiga } from '@taiga-ui/core';
import { provideEventPlugins } from '@taiga-ui/event-plugins';
import { API_CLIENT } from '../../core/api/api';
import { chooseOption, enterNumber, optionLabels } from '../../core/ui/testing';
import { IndicatorPanel } from './indicator-panel';
import { IndicatorsStore } from './indicators.store';

const ok = <T>(data: T) => Promise.resolve({ data, response: { status: 200 } });

const CATALOG = [
  {
    name: 'ema',
    title: 'EMA — экспоненциальная скользящая средняя',
    version: 1,
    pane: 'price',
    outputs: ['value'],
    warmup_bars: 20,
    params_schema: {
      properties: {
        period: { type: 'integer', minimum: 1, description: 'Период, баров' },
      },
    },
    defaults: { period: 20 },
  },
];

@Component({
  imports: [IndicatorPanel],
  providers: [IndicatorsStore],
  template: `<app-indicator-panel [chartTimeframe]="tf()" />`,
})
class Host {
  tf = signal('15m');
}

async function settle(fixture: ComponentFixture<unknown>): Promise<void> {
  for (let i = 0; i < 4; i++) {
    await new Promise((resolve) => setTimeout(resolve));
    fixture.detectChanges();
  }
}

describe('IndicatorPanel', () => {
  async function mount() {
    const client = {
      GET: vi.fn((path: string) => {
        if (path === '/indicators') {
          return ok(CATALOG);
        }
        return Promise.resolve({
          error: { detail: 'нет данных' },
          response: { status: 404 },
        });
      }),
    };
    TestBed.configureTestingModule({
      imports: [Host],
      providers: [
        provideEventPlugins(),
        provideTaiga(),
        { provide: API_CLIENT, useValue: client },
      ],
    });
    const fixture = TestBed.createComponent(Host);
    fixture.detectChanges();
    const store =
      fixture.debugElement.children[0].injector.get(IndicatorsStore);
    await store.loadCatalog();
    await settle(fixture);
    return { fixture, store, root: fixture.nativeElement as HTMLElement };
  }

  const TYPE = 'app-select[aria-label="Тип индикатора"]';
  const SOURCE = 'app-select[aria-label="Source TF"]';

  it('после выбора типа показывает форму параметров и source TF не младше графика', async () => {
    const { fixture, root } = await mount();

    chooseOption(fixture, TYPE, 'EMA — экспоненциальная скользящая средняя');
    await settle(fixture);

    expect(root.textContent).toContain('Период, баров');
    const period = root.querySelector('app-number input') as HTMLInputElement;
    expect(period.value).toBe('20');
    expect(optionLabels(fixture, SOURCE)).toEqual([
      '15m',
      '1h',
      '4h',
      '1d',
      '1w',
    ]);
  });

  it('на старшем графике младшие source TF недоступны', async () => {
    const { fixture, root } = await mount();
    fixture.componentInstance.tf.set('4h');
    fixture.detectChanges();
    chooseOption(fixture, TYPE, 'EMA — экспоненциальная скользящая средняя');
    await settle(fixture);

    expect(optionLabels(fixture, SOURCE)).toEqual(['4h', '1d', '1w']);
  });

  it('«Добавить» кладёт индикатор с введёнными параметрами и source TF', async () => {
    const { fixture, store, root } = await mount();
    chooseOption(fixture, TYPE, 'EMA — экспоненциальная скользящая средняя');
    await settle(fixture);
    enterNumber(fixture, 'app-number', 200);
    chooseOption(fixture, SOURCE, '1h');
    fixture.detectChanges();

    (
      Array.from(root.querySelectorAll('button')).find(
        (b) => b.textContent?.trim() === 'Добавить',
      ) as HTMLButtonElement
    ).click();
    await settle(fixture);

    expect(store.active()).toHaveLength(1);
    expect(store.active()[0]).toMatchObject({
      name: 'ema',
      params: { period: 200 },
      sourceTimeframe: '1h',
    });
    expect(root.textContent).toContain('EMA(200) · 1h');
  });

  it('Verify показывает отчёт: совпадение и расхождения', async () => {
    const { fixture, store, root } = await mount();
    store.verifyResult.set({
      timeframe: '15m',
      bars: 50,
      range: ['2026-09-28T04:00:00Z', '2026-09-28T16:00:00Z'],
      ok: false,
      reports: [
        {
          indicator: 'sma',
          params: { period: 5 },
          bars: 50,
          positions_checked: 50,
          values_checked: 50,
          mismatch_count: 0,
          ok: true,
          mismatches: [],
        },
        {
          indicator: 'leaky',
          params: {},
          bars: 50,
          positions_checked: 50,
          values_checked: 50,
          mismatch_count: 2,
          ok: false,
          mismatches: [
            {
              index: 7,
              timestamp: '2026-09-28T05:45:00Z',
              output: 'value',
              batch: 1,
              online: 2,
            },
          ],
        },
      ],
    });
    await settle(fixture);

    const text = (root.textContent ?? '').replace(/\s+/g, ' ');
    expect(text).toContain('Найдены расхождения');
    expect(text).toContain('sma(5): совпало 50 значений в 50 позициях');
    expect(text).toContain('расхождений 2; первое — бар 7');
    expect(text).toContain('batch 1 ≠ online 2');
  });

  it('крестик убирает индикатор', async () => {
    const { fixture, store, root } = await mount();
    await store.add(CATALOG[0] as never, { period: 50 }, '15m');
    await settle(fixture);

    (root.querySelector('button.remove') as HTMLButtonElement).click();
    await settle(fixture);

    expect(store.active()).toEqual([]);
    expect(root.querySelector('.chip')).toBeNull();
  });
});
