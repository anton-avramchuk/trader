import { ComponentFixture, TestBed } from '@angular/core/testing';
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
  function setup() {
    const client = {
      GET: vi.fn((path: string) => {
        if (path === '/indicators' || path === '/chart-profiles') {
          return ok([]);
        }
        if (path === '/roots') {
          return ok([{ id: 1, code: 'NG', name: 'Gas' }]);
        }
        if (path === '/roots/{root_id}/contracts') {
          return ok([
            {
              id: 10,
              root_id: 1,
              secid: 'NGZ6',
              expiration_date: '2026-12-29',
            },
          ]);
        }
        return ok({
          candles: [
            {
              timestamp: '2026-09-28T04:00:00Z',
              close_time: '2026-09-28T14:00:00Z',
              open: '3.1',
              high: '3.3',
              low: '3.0',
              close: '3.2',
              volume: '100',
              is_partial: true,
              contract_id: 10,
              price_factor: '1.0054',
            },
          ],
          rolls: [],
          truncated: true,
        });
      }),
    };
    TestBed.configureTestingModule({
      imports: [Chart],
      providers: [
        provideEventPlugins(),
        provideTaiga(),
        { provide: API_CLIENT, useValue: client },
      ],
    });
    return { client, fixture: TestBed.createComponent(Chart) };
  }

  it('показывает выбор серии, таймфреймы и легенду последнего бара в МСК', async () => {
    const { fixture } = setup();
    fixture.detectChanges();
    await settle(fixture);
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';

    for (const tf of ['15m', '1h', '4h', '1d', '1w']) {
      expect(text).toContain(tf);
    }
    expect(text).toContain('Continuous');
    expect(text).toContain('NGZ6 (в своих ценах)');
    expect(text).toContain('28.09.2026, 07:00 МСК');
    expect(text).toContain('неполный бар');
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
});
