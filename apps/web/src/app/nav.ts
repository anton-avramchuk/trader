export interface NavItem {
  path: string;
  title: string;
  icon: string;
}

export interface NavGroup {
  title: string;
  items: NavItem[];
}

/** Разделы приложения: Data (Instruments), Chart и заготовки. */
export const NAV_GROUPS: NavGroup[] = [
  {
    title: 'Data',
    items: [
      { path: '/data/instruments', title: 'Instruments', icon: '@tui.layers' },
    ],
  },
  {
    title: 'Analysis',
    items: [
      { path: '/chart', title: 'Chart', icon: '@tui.chart-candlestick' },
      { path: '/replay', title: 'Replay', icon: '@tui.play' },
      { path: '/research', title: 'Research', icon: '@tui.flask-conical' },
      { path: '/backtest', title: 'Backtest', icon: '@tui.history' },
    ],
  },
];

export type NavEntry =
  | { kind: 'title'; key: string; title: string }
  | ({ kind: 'link'; key: string } & NavItem);

/** Плоский список для шаблона: заголовок группы, затем её пункты. */
export function navEntries(groups: NavGroup[] = NAV_GROUPS): NavEntry[] {
  return groups.flatMap((group) => [
    { kind: 'title' as const, key: `g:${group.title}`, title: group.title },
    ...group.items.map((item) => ({
      kind: 'link' as const,
      key: item.path,
      ...item,
    })),
  ]);
}
