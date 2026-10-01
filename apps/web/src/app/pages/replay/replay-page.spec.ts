import { ComponentFixture, TestBed } from '@angular/core/testing';
import {
  ActivatedRoute,
  convertToParamMap,
  provideRouter,
} from '@angular/router';
import { provideTaiga } from '@taiga-ui/core';
import { provideEventPlugins } from '@taiga-ui/event-plugins';
import { API_CLIENT } from '../../core/api/api';
import { Replay } from './replay-page';

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

function bar(index: number) {
  const start = Date.UTC(2026, 8, 28, 0) + index * 3600_000;
  return {
    timestamp: new Date(start).toISOString(),
    close_time: new Date(start + 3600_000).toISOString(),
    open: '100',
    high: '101',
    low: '99',
    close: '100',
    volume: '1',
    is_partial: false,
  };
}

describe('Replay', () => {
  function setup(query: Record<string, string> = {}) {
    const client = {
      GET: vi.fn(
        (path: string, request?: { params: { query: { tail?: boolean } } }) => {
          if (path === '/indicators' || path === '/chart-profiles') {
            return ok([]);
          }
          if (path === '/roots') {
            return ok([{ id: 1, code: 'NG' }]);
          }
          if (path === '/roots/{root_id}/contracts') {
            return ok([]);
          }
          const candles = request?.params.query.tail
            ? [bar(-2), bar(-1)]
            : [bar(0), bar(1), bar(2)];
          return ok({ candles, rolls: [], truncated: false, next_start: null });
        },
      ),
    };
    TestBed.configureTestingModule({
      imports: [Replay],
      providers: [
        provideEventPlugins(),
        provideTaiga(),
        provideRouter([]),
        { provide: API_CLIENT, useValue: client },
        {
          provide: ActivatedRoute,
          useValue: { snapshot: { queryParamMap: convertToParamMap(query) } },
        },
      ],
    });
    return TestBed.createComponent(Replay);
  }

  function button(root: HTMLElement, text: string): HTMLButtonElement {
    const found = Array.from(root.querySelectorAll('button')).find((b) =>
      b.textContent?.includes(text),
    );
    if (!found) {
      throw new Error(`Нет кнопки «${text}»`);
    }
    return found;
  }

  async function begin(fixture: ComponentFixture<Replay>) {
    fixture.detectChanges();
    await settle(fixture);
    const root = fixture.nativeElement as HTMLElement;
    const input = root.querySelector('input[type="date"]') as HTMLInputElement;
    input.value = '2026-09-28';
    input.dispatchEvent(new Event('input'));
    fixture.detectChanges();
    button(root, 'Начать').click();
    await settle(fixture);
    return root;
  }

  it('ссылка из бэктеста сразу начинает воспроизведение с дня входа', async () => {
    const fixture = setup({ root: '1', tf: '15m', date: '2026-09-28' });

    fixture.detectChanges();
    await settle(fixture);

    const root = fixture.nativeElement as HTMLElement;
    expect(root.textContent).toContain('баров 2 из 5 (будущее скрыто)');
    expect(
      (root.querySelector('input[type="date"]') as HTMLInputElement).value,
    ).toBe('2026-09-28');
  });

  it('ссылка с неизвестным инструментом ничего не запускает', async () => {
    const fixture = setup({ root: '99', tf: '1h', date: '2026-09-28' });

    fixture.detectChanges();
    await settle(fixture);

    expect((fixture.nativeElement as HTMLElement).textContent).toContain(
      'Выберите дату',
    );
  });

  it('без данных управление недоступно и есть подсказка', async () => {
    const fixture = setup();
    fixture.detectChanges();
    await settle(fixture);
    const root = fixture.nativeElement as HTMLElement;

    expect(root.textContent).toContain('Выберите дату');
    expect(button(root, 'Next candle').disabled).toBe(true);
    expect(button(root, 'Restart').disabled).toBe(true);
  });

  it('после старта видно 2 бара из 5, «Next» открывает следующий', async () => {
    const fixture = setup();
    const root = await begin(fixture);

    expect(root.textContent).toContain('баров 2 из 5 (будущее скрыто)');
    expect(root.textContent).toContain('Система знает на');

    button(root, 'Next candle').click();
    await settle(fixture);
    expect(root.textContent).toContain('баров 3 из 5');

    button(root, 'Prev').click();
    button(root, 'Prev').click();
    await settle(fixture);
    expect(root.textContent).toContain('баров 1 из 5');
  });

  it('Restart возвращает к точке старта', async () => {
    const fixture = setup();
    const root = await begin(fixture);
    button(root, '+10').click();
    await settle(fixture);
    expect(root.textContent).toContain('баров 5 из 5');
    expect(root.textContent).toContain('Данные закончились');

    button(root, 'Restart').click();
    await settle(fixture);

    expect(root.textContent).toContain('баров 2 из 5');
  });
});
