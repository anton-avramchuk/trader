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
    loadComponent: () =>
      import('./pages/backtest/backtest').then((m) => m.Backtest),
  },
  { path: '**', redirectTo: 'data/instruments' },
];
