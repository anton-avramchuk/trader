import { ComponentFixture, TestBed } from '@angular/core/testing';
import type { Backtest } from '@trader/api-client';
import { provideRouter } from '@angular/router';
import { provideTaiga } from '@taiga-ui/core';
import { provideEventPlugins } from '@taiga-ui/event-plugins';
import { of } from 'rxjs';
import { API_CLIENT } from '../../core/api/api';
import { JobsService } from '../../core/jobs/jobs';
import { enterDate, enterText } from '../../core/ui/testing';
import { Backtest as BacktestPage } from './backtest';

const ok = (data: unknown) =>
  Promise.resolve({ data, response: { status: 200 } });

async function settle(fixture: ComponentFixture<unknown>): Promise<void> {
  for (let i = 0; i < 5; i++) {
    await new Promise((resolve) => setTimeout(resolve));
    fixture.detectChanges();
  }
}

const metrics = {
  trades: 3,
  net: 12,
  gross: 14,
  average_trade: 4,
  win_rate: 0.667,
  profit_factor: 2.5,
  max_drawdown: 3,
  sharpe: 1.1,
  sortino: 1.4,
  average_mfe: 6,
  average_mae: -2,
  ambiguous_share: 0,
  warnings: ['small_sample'],
};

const done = (kind: 'single' | 'walk_forward'): Backtest =>
  ({
    id: 7,
    kind,
    status: 'succeeded',
    job_id: 70,
    timeframe_code: '1h',
    family: 'pattern',
    period_from: '2026-01-01',
    period_to: '2026-06-30',
    params_hash: 'abcdef012345',
    result: {
      metrics: { ticks: metrics, points: metrics, money: metrics },
      equity: { ticks: [{ day: '2026-01-02', equity: 5 }], money: [] },
      runs_for_series: 4,
      walk_forward: { final_params: { stop_atr: 1.5 } },
      test: { status: 'rejected' },
    },
  }) as unknown as Backtest;

describe('Backtest page', () => {
  function setup(kind: 'single' | 'walk_forward' = 'single') {
    const client = {
      GET: vi.fn((path: string) => {
        switch (path) {
          case '/instruments':
            return ok([{ id: 1, ticker: 'SBER' }]);
          case '/backtests':
            return ok([done(kind)]);
          case '/backtest-locks':
            return ok([
              {
                id: 1,
                instrument_id: 1,
                timeframe_code: '1h',
                family: 'pattern',
                test_from: '2026-07-01',
                test_to: '2026-09-01',
              },
            ]);
          case '/backtests/{experiment_id}':
            return ok(done(kind));
          case '/backtests/{experiment_id}/trades':
            return ok([
              {
                id: 1,
                segment: 'single',
                side: 'long',
                ref: 'double_triple:1',
                entry_time: '2026-01-02T10:00:00Z',
                exit_time: '2026-01-02T12:00:00Z',
                reason: 'target',
                gross_ticks: 5,
                cost_ticks: 1,
                net_points: 0.04,
                net_money: 80,
                ambiguous_bar: true,
              },
            ]);
          case '/backtests/{experiment_id}/windows':
            return ok([
              {
                id: 2,
                train_from: '2026-01-01',
                train_to: '2026-02-01',
                valid_from: '2026-02-02',
                valid_to: '2026-03-01',
                params: { stop_atr: 1.5 },
                train_metrics: { trades: 10 },
                valid_metrics: { trades: 4, net: 2 },
              },
            ]);
          default:
            return ok([]);
        }
      }),
      POST: vi.fn((path: string) =>
        path === '/backtests'
          ? ok({ ...done(kind), status: 'queued' })
          : ok({ unlocked: true }),
      ),
    };
    TestBed.configureTestingModule({
      providers: [
        provideTaiga(),
        provideEventPlugins(),
        provideRouter([]),
        { provide: API_CLIENT, useValue: client },
        {
          provide: JobsService,
          useValue: { watch: () => of({ progress: 1 }) },
        },
      ],
    });
    const fixture = TestBed.createComponent(BacktestPage);
    return { client, fixture, el: fixture.nativeElement as HTMLElement };
  }

  it('показывает форму, историю и заблокированные test', async () => {
    const { fixture, el } = setup();

    fixture.detectChanges();
    await settle(fixture);

    expect(el.querySelector('h1')?.textContent).toBe('Backtest');
    expect(el.textContent).toContain('Последние бэктесты');
    expect(el.textContent).toContain('2026-07-01 … 2026-09-01');
    expect(el.querySelector('app-select[name="kind"]')).toBeTruthy();
    expect(el.querySelector('textarea[name="grid"]')).toBeNull(); // только WF
  });

  it('запуск: метрики, предупреждение, equity и сделки', async () => {
    const { fixture, client, el } = setup();
    fixture.detectChanges();
    await settle(fixture);
    enterDate(fixture, 'app-date[name="from"]', '2026-01-01');
    enterDate(fixture, 'app-date[name="to"]', '2026-06-30');
    fixture.detectChanges();

    el.querySelector<HTMLFormElement>('form')!.dispatchEvent(
      new Event('submit'),
    );
    await settle(fixture);

    expect(client.POST).toHaveBeenCalledWith(
      '/backtests',
      expect.objectContaining({
        body: expect.objectContaining({ kind: 'single', instrument_id: 1 }),
      }),
    );
    const text = el.textContent ?? '';
    expect(text).toContain('Бэктест №7');
    expect(text).toContain('Profit factor');
    expect(text).toContain('2.50');
    expect(text).toContain('мало сделок');
    expect(text).toContain('запусков по связке: 4');
    expect(el.querySelector('svg.equity')).toBeTruthy();
    expect(text).toContain('неоднозначный бар');
    expect(text).toContain('Сделки (1)');
  });

  it('переключение единиц меняет сделки', async () => {
    const { fixture, el } = setup();
    fixture.detectChanges();
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('.history button')!.click();
    await settle(fixture);

    const money = Array.from(
      el.querySelectorAll<HTMLButtonElement>('.unit'),
    ).find((b) => b.textContent?.trim() === 'деньги');
    money!.click();
    fixture.detectChanges();

    expect(el.querySelector('.trades')?.textContent).toContain('80.00');
  });

  it('walk-forward: окна, финальные параметры, отклонённый test', async () => {
    const { fixture, el } = setup('walk_forward');
    fixture.detectChanges();
    await settle(fixture);

    el.querySelector<HTMLButtonElement>('.history button')!.click();
    await settle(fixture);

    const text = el.textContent ?? '';
    expect(text).toContain('Окна walk-forward');
    expect(text).toContain('stop_atr=1.5');
    expect(text).toContain('Финальные параметры');
    expect(text).toContain('уже открывали');
  });

  it('разблокировка требует причину', async () => {
    const { fixture, client, el } = setup();
    fixture.detectChanges();
    await settle(fixture);

    el.querySelector<HTMLButtonElement>('.lock button')!.click();
    fixture.detectChanges();
    const confirm = el.querySelector<HTMLButtonElement>('.unlock button')!;
    expect(confirm.disabled).toBe(true);
    enterText(fixture, '.unlock app-text', 'ошибка в данных');
    fixture.detectChanges();
    await settle(fixture);
    el.querySelector<HTMLButtonElement>('.unlock button')!.click();
    await settle(fixture);

    expect(client.POST).toHaveBeenCalledWith(
      '/backtest-locks/unlock',
      expect.objectContaining({
        body: expect.objectContaining({ note: 'ошибка в данных' }),
      }),
    );
  });
});
