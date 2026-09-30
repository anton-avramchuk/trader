import { Route } from '@angular/router';

const placeholder = () =>
  import('./pages/placeholder').then((m) => m.Placeholder);

export const appRoutes: Route[] = [
  { path: '', pathMatch: 'full', redirectTo: 'data/instruments' },
  {
    path: 'data/instruments',
    loadComponent: () =>
      import('./pages/instruments/instruments').then((m) => m.Instruments),
  },
  {
    path: 'data/import',
    loadComponent: () => import('./pages/import/import').then((m) => m.Import),
  },
  {
    path: 'data/quality',
    loadComponent: placeholder,
    data: { title: 'Quality', note: 'Покрытие, пропуски и конфликты данных.' },
  },
  {
    path: 'chart',
    loadComponent: () => import('./pages/chart/chart').then((m) => m.Chart),
  },
  {
    path: 'replay',
    loadComponent: () =>
      import('./pages/replay/replay-page').then((m) => m.Replay),
  },
  {
    path: 'research',
    loadComponent: placeholder,
    data: {
      title: 'Research',
      note: 'Swing, уровни, паттерны, статистика исходов.',
    },
  },
  {
    path: 'backtest',
    loadComponent: placeholder,
    data: { title: 'Backtest', note: 'Тестирование стратегий.' },
  },
  { path: '**', redirectTo: 'data/instruments' },
];
