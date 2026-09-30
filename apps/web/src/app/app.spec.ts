import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { provideTaiga } from '@taiga-ui/core';
import { provideEventPlugins } from '@taiga-ui/event-plugins';
import { API_CLIENT } from './core/api/api';
import { App } from './app';
import { appRoutes } from './app.routes';

describe('App', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [
        provideEventPlugins(),
        provideTaiga(),
        provideRouter(appRoutes),
        {
          provide: API_CLIENT,
          useValue: {
            GET: () => Promise.resolve({ data: [], response: { status: 200 } }),
          },
        },
      ],
    }).compileComponents();
  });

  it('показывает разделы навигации', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';

    for (const title of [
      'Data',
      'Instruments',
      'Import',
      'Quality',
      'Chart',
      'Research',
      'Backtest',
    ]) {
      expect(text).toContain(title);
    }
    expect(text).toContain('МСК');
  });

  it('открывает Instruments по умолчанию и переключает разделы', async () => {
    const fixture = TestBed.createComponent(App);
    const router = TestBed.inject(Router);
    await router.navigateByUrl('/');
    await fixture.whenStable();

    expect(router.url).toBe('/data/instruments');
    const main = (fixture.nativeElement as HTMLElement).querySelector('main');
    expect(main?.textContent).toContain('Instruments');

    await router.navigateByUrl('/backtest');
    await fixture.whenStable();
    expect(main?.textContent).toContain('Backtest');
  });

  it('неизвестный адрес ведёт на стартовый раздел', async () => {
    const fixture = TestBed.createComponent(App);
    const router = TestBed.inject(Router);

    await router.navigateByUrl('/no/such/page');
    await fixture.whenStable();

    expect(router.url).toBe('/data/instruments');
  });
});
