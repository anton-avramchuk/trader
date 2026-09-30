import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideTaiga } from '@taiga-ui/core';
import { provideEventPlugins } from '@taiga-ui/event-plugins';
import { API_CLIENT } from '../../core/api/api';
import { JobsService } from '../../core/jobs/jobs';
import { Instruments } from './instruments';

const ok = <T>(data: T) => Promise.resolve({ data, response: { status: 200 } });

/** Ждёт асинхронные загрузки store (промисы фейкового клиента) и обновляет вид. */
async function settle(fixture: ComponentFixture<unknown>): Promise<void> {
  for (let i = 0; i < 5; i++) {
    await new Promise((resolve) => setTimeout(resolve));
    fixture.detectChanges();
  }
}

describe('Instruments', () => {
  function setup(roots: unknown[]) {
    const client = {
      GET: vi.fn((path: string) => {
        if (path === '/roots') {
          return ok(roots);
        }
        if (path === '/roots/{root_id}/contracts') {
          return ok([
            {
              id: 10,
              root_id: 1,
              expiration_date: '2026-12-29',
              last_trade_date: '2026-12-29',
              secid: 'NGZ6',
              provider_ids: [
                { provider: 'moex_iss', id_type: 'secid', external_id: 'NGZ6' },
              ],
            },
          ]);
        }
        if (path === '/calendars/{code}') {
          return ok({
            code: 'moex_forts',
            name: 'MOEX FORTS',
            fingerprint: 'a'.repeat(64),
            rule_list: [
              {
                effective_from: '2026-03-23',
                effective_to: null,
                bar_anchor: '07:00:00',
                weekday_windows: [
                  { name: 'main', start: '09:00:00', end: '23:50:00' },
                ],
                weekend_windows: [],
              },
            ],
          });
        }
        return ok({ holidays: ['2026-01-01', '2026-01-02'], special_days: [] });
      }),
      POST: vi.fn(),
      PATCH: vi.fn(),
      DELETE: vi.fn(),
    };
    TestBed.configureTestingModule({
      imports: [Instruments],
      providers: [
        provideEventPlugins(),
        provideTaiga(),
        { provide: API_CLIENT, useValue: client },
        { provide: JobsService, useValue: { watch: vi.fn() } },
      ],
    });
    return TestBed.createComponent(Instruments);
  }

  const root = {
    id: 1,
    code: 'NG',
    name: 'Природный газ',
    exchange: 'MOEX',
    quote_currency: 'USD',
    tick_size: '0.00100000',
    calendar_code: 'moex_forts',
    roll_trading_days: 5,
    include_weekend_sessions: false,
    created_at: '2026-09-30T00:00:00Z',
  };

  it('показывает root, контракты и календарь', async () => {
    const fixture = setup([root]);
    fixture.detectChanges();
    await settle(fixture);
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';

    expect(text).toContain('NG — Природный газ');
    expect(text).toContain('за 5 торг. дн. до экспирации');
    expect(text).toContain('исключены');
    expect(text).toContain('NGZ6');
    expect(text).toContain('moex_iss: NGZ6');
    expect(text).toContain('MOEX FORTS');
    expect(text).toContain('09:00–23:50 main');
    expect(text).toContain('Праздники (нет торгов): 2');
  });

  it('без root подсказывает завести первый', async () => {
    const fixture = setup([]);
    fixture.detectChanges();
    await settle(fixture);

    expect((fixture.nativeElement as HTMLElement).textContent).toContain(
      'Базовых активов пока нет',
    );
  });

  it('кнопка «Новый root» открывает форму создания', async () => {
    const fixture = setup([root]);
    fixture.detectChanges();
    await settle(fixture);
    const element = fixture.nativeElement as HTMLElement;

    const button = Array.from(element.querySelectorAll('button')).find((b) =>
      b.textContent?.includes('Новый root'),
    );
    button?.click();
    fixture.detectChanges();

    expect(element.textContent).toContain('Новый базовый актив');
    expect(element.querySelector('app-root-form')).not.toBeNull();
  });
});
