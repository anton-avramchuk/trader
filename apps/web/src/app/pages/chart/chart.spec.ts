import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { provideTaiga } from '@taiga-ui/core';
import { provideEventPlugins } from '@taiga-ui/event-plugins';
import { API_CLIENT } from '../../core/api/api';
import { Chart } from './chart';

vi.mock('lightweight-charts', () => {
  const series = { setData: vi.fn() };
  const timeScale = {
    subscribeVisibleLogicalRangeChange: vi.fn(),
    fitContent: vi.fn(),
    scrollToRealTime: vi.fn(),
    getVisibleRange: vi.fn(),
    setVisibleRange: vi.fn(),
    getVisibleLogicalRange: vi.fn(() => ({ from: 0, to: 20 })),
    setVisibleLogicalRange: vi.fn(),
  };
  return {
    CandlestickSeries: 'candles',
    HistogramSeries: 'volume',
    LineSeries: 'line',
    createChart: vi.fn(() => ({
      addSeries: vi.fn(() => series),
      priceScale: vi.fn(() => ({ applyOptions: vi.fn() })),
      timeScale: vi.fn(() => timeScale),
      subscribeCrosshairMove: vi.fn(),
      subscribeClick: vi.fn(),
      removeSeries: vi.fn(),
      remove: vi.fn(),
    })),
    createSeriesMarkers: vi.fn(() => ({ setMarkers: vi.fn() })),
  };
});

const ok = <T>(data: T) => Promise.resolve({ data, response: { status: 200 } });

async function settle(fixture: ComponentFixture<unknown>): Promise<void> {
  for (let i = 0; i < 5; i++) {
    await new Promise((resolve) => setTimeout(resolve));
    fixture.detectChanges();
  }
}

describe('Chart', () => {
  const bar = (timestamp: string, close: string): Record<string, string> => ({
    timestamp,
    close_time: '2026-09-28T14:00:00Z',
    open: close,
    high: close,
    low: close,
    close,
    volume: '100',
    trading_day: '2026-09-28',
  });

  function setup(
    candles: Record<string, string>[] = [bar('2026-09-28T04:00:00Z', '3.2')],
  ) {
    const client = {
      GET: vi.fn(
        (path: string, options?: { params?: { query?: { end?: string } } }) => {
          if (path === '/indicators' || path === '/chart-profiles') {
            return ok([]);
          }
          if (path === '/instruments') {
            return ok([{ id: 1, ticker: 'SBER', name: 'Сбербанк' }]);
          }
          if (options?.params?.query?.end) {
            // страница истории: одна свеча раньше и конец данных
            return ok({
              candles: [bar('2026-09-01T04:00:00Z', '1')],
              truncated: false,
            });
          }
          return ok({ candles, truncated: true });
        },
      ),
    };
    TestBed.configureTestingModule({
      imports: [Chart],
      providers: [
        provideEventPlugins(),
        provideRouter([]),
        provideTaiga(),
        { provide: API_CLIENT, useValue: client },
      ],
    });
    return { client, fixture: TestBed.createComponent(Chart) };
  }

  const text = (fixture: ComponentFixture<unknown>): string =>
    (fixture.nativeElement as HTMLElement).textContent ?? '';
  const press = (code: string, init: KeyboardEventInit = {}): void => {
    window.dispatchEvent(new KeyboardEvent('keydown', { code, ...init }));
  };
  const candleQueries = (client: { GET: ReturnType<typeof vi.fn> }) =>
    (
      client.GET.mock.calls as unknown as [
        string,
        { params: { query: { timeframe: string } } }?,
      ][]
    )
      .filter(([path]) => path === '/candles')
      .map(([, options]) => options?.params.query.timeframe);

  beforeEach(() => localStorage.clear());

  it('показывает выбор инструмента, таймфреймы и легенду последнего бара в МСК', async () => {
    const { fixture } = setup();
    fixture.detectChanges();
    await settle(fixture);
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';

    for (const tf of ['15m', '1h', '4h', '1d', '1w']) {
      expect(text).toContain(tf);
    }
    expect(
      (fixture.nativeElement as HTMLElement).querySelector<HTMLInputElement>(
        'app-select[aria-label="Инструмент"] input',
      )?.value,
    ).toBe('SBER');
    expect(text).toContain('28.09.2026, 07:00 МСК');
    expect(text).toContain('TradingView');
    expect(text).toContain('Прокрутите влево');
  });

  it('переключение таймфрейма запрашивает новые бары', async () => {
    const { client, fixture } = setup();
    fixture.detectChanges();
    await settle(fixture);
    const button = Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll('button'),
    ).find((b) => b.textContent?.trim() === '4h');

    button?.click();
    await settle(fixture);

    const calls = client.GET.mock.calls as unknown as [
      string,
      { params: { query: { timeframe: string } } }?,
    ][];
    const queries = calls
      .filter(([path]) => path === '/candles')
      .map(([, options]) => options?.params.query.timeframe);
    expect(queries).toEqual(['1d', '4h']);
  });

  it('легенда: изменение бара в процентах и значения индикаторов не ломают показ без индикаторов', async () => {
    const { fixture } = setup([
      bar('2026-09-28T04:00:00Z', '100'),
      bar('2026-09-29T04:00:00Z', '102'),
    ]);
    fixture.detectChanges();
    await settle(fixture);

    expect(text(fixture)).toContain('+2.00%');
  });

  it('горячие клавиши: цифра выбирает таймфрейм', async () => {
    const { client, fixture } = setup();
    fixture.detectChanges();
    await settle(fixture);

    press('Digit3');
    await settle(fixture);

    expect(candleQueries(client)).toEqual(['1d', '4h']);
  });

  it('горячие клавиши не срабатывают в полях ввода и с Ctrl', async () => {
    const { client, fixture } = setup();
    fixture.detectChanges();
    await settle(fixture);

    press('Digit3', { ctrlKey: true });
    const input = document.createElement('input');
    document.body.append(input);
    input.dispatchEvent(
      new KeyboardEvent('keydown', {
        code: 'Digit2',
        bubbles: true,
      }),
    );
    input.remove();
    await settle(fixture);

    expect(candleQueries(client)).toEqual(['1d']);
  });

  it('H включает горизонталь, Esc отменяет; кнопка и клавиша работают одинаково', async () => {
    const { fixture } = setup();
    fixture.detectChanges();
    await settle(fixture);

    press('KeyH');
    fixture.detectChanges();
    expect(text(fixture)).toContain('Кликните по графику');

    press('Escape');
    fixture.detectChanges();
    expect(text(fixture)).not.toContain('Кликните по графику');

    const button = Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll('button'),
    ).find((b) => b.textContent?.trim() === 'Тренд');
    button?.click();
    fixture.detectChanges();
    expect(text(fixture)).toContain('по двум точкам');
  });

  it('вид запоминается: таймфрейм и инструмент восстанавливаются', async () => {
    localStorage.setItem(
      'trader.chart.view.v1',
      JSON.stringify({ instrumentId: 1, timeframe: '1h', logScale: true }),
    );
    const { client, fixture } = setup();
    fixture.detectChanges();
    await settle(fixture);

    expect(candleQueries(client)).toEqual(['1h']);
    expect(
      (fixture.nativeElement as HTMLElement).querySelector<HTMLInputElement>(
        '.tools input[type="checkbox"]',
      )?.checked,
    ).toBe(true);

    press('Digit4');
    await settle(fixture);
    const saved = JSON.parse(
      localStorage.getItem('trader.chart.view.v1') ?? '{}',
    ) as { timeframe: string; instrumentId: number };
    expect(saved).toMatchObject({ timeframe: '1d', instrumentId: 1 });
  });

  it('клик по бару открывает ссылку на Replay с его дня', async () => {
    const { fixture } = setup();
    fixture.detectChanges();
    await settle(fixture);
    const root = fixture.nativeElement as HTMLElement;
    expect(root.querySelector('a[href*="/replay"]')).toBeNull();

    (
      fixture.componentInstance as unknown as {
        onPoint(point: { time: number; price: number }): void;
      }
    ).onPoint({ time: Date.UTC(2026, 8, 28, 4, 0) / 1000, price: 3 });
    fixture.detectChanges();

    const link = root.querySelector<HTMLAnchorElement>('a[href*="/replay"]');
    expect(link?.textContent).toContain('2026-09-28');
    expect(link?.getAttribute('href')).toContain(
      'instrument=1&tf=1d&date=2026-09-28',
    );
  });

  it('переход к дате подгружает историю до неё', async () => {
    const { client, fixture } = setup();
    fixture.detectChanges();
    await settle(fixture);

    await (
      fixture.componentInstance as unknown as {
        goToDate(date: string): Promise<void>;
      }
    ).goToDate('2026-09-10');

    const withEnd = (
      client.GET.mock.calls as unknown as [
        string,
        { params: { query: { end?: string } } }?,
      ][]
    ).filter(([path, o]) => path === '/candles' && o?.params.query.end);
    expect(withEnd).toHaveLength(1);
  });

  it('«Догрузить» без загруженных свечей подсказывает, что делать', async () => {
    const { fixture } = setup();
    fixture.detectChanges();
    await settle(fixture);
    const button = Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll('button'),
    ).find((b) => b.textContent?.trim() === 'Догрузить');

    button?.click();
    await settle(fixture);

    expect(text(fixture)).toContain('загрузите период на странице Instruments');
  });
});
