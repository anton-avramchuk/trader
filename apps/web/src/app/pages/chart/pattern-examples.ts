/** Схемы паттернов для галереи «Примеры» (условные координаты 90×60, вверх — рост цены). */

export interface PatternExample {
  pattern: string;
  direction: 'bullish' | 'bearish';
  /** Ход цены через swing-точки. */
  path: [number, number][];
  /** Шея и границы: [x1, y1, x2, y2]. */
  lines: [number, number, number, number][];
  /** Точка подтверждения пробоем. */
  breakout: [number, number];
  note: string;
}

const flip = (path: [number, number][]): [number, number][] =>
  path.map(([x, y]) => [x, 60 - y]);
const flipLines = (
  lines: [number, number, number, number][],
): [number, number, number, number][] =>
  lines.map(([x1, y1, x2, y2]) => [x1, 60 - y1, x2, 60 - y2]);

const HS: PatternExample = {
  pattern: 'head_shoulders',
  direction: 'bearish',
  path: [
    [0, 8],
    [12, 30],
    [22, 16],
    [38, 46],
    [54, 16],
    [64, 30],
    [76, 8],
  ],
  lines: [[22, 16, 84, 16]],
  breakout: [76, 8],
  note: 'Голова выше обоих плеч; подтверждение — закрытие ниже шеи.',
};

const DOUBLE_TOP: PatternExample = {
  pattern: 'double_top',
  direction: 'bearish',
  path: [
    [0, 8],
    [16, 42],
    [32, 20],
    [48, 42],
    [64, 8],
  ],
  lines: [[32, 20, 84, 20]],
  breakout: [64, 8],
  note: 'Две равные вершины; пробой впадины вниз.',
};

const TRIPLE_TOP: PatternExample = {
  pattern: 'triple_top',
  direction: 'bearish',
  path: [
    [0, 8],
    [10, 42],
    [22, 22],
    [34, 42],
    [46, 22],
    [58, 42],
    [72, 8],
  ],
  lines: [[22, 22, 84, 22]],
  breakout: [72, 8],
  note: 'Три равные вершины; пробой шеи вниз.',
};

const TRIANGLE_ASC: PatternExample = {
  pattern: 'triangle_ascending',
  direction: 'bullish',
  path: [
    [0, 8],
    [12, 42],
    [24, 18],
    [38, 42],
    [50, 30],
    [62, 42],
    [74, 52],
  ],
  lines: [
    [12, 42, 84, 42],
    [0, 8, 62, 34],
  ],
  breakout: [74, 52],
  note: 'Плоское сопротивление и растущая поддержка; пробой вверх.',
};

const TRIANGLE_SYM: PatternExample = {
  pattern: 'triangle_symmetric',
  direction: 'bullish',
  path: [
    [0, 30],
    [10, 54],
    [22, 12],
    [34, 46],
    [46, 22],
    [56, 38],
    [68, 52],
  ],
  lines: [
    [10, 54, 62, 36],
    [22, 12, 62, 28],
  ],
  breakout: [68, 52],
  note: 'Сходящиеся границы; пробой любой из них (два вхождения).',
};

const WEDGE_RISING: PatternExample = {
  pattern: 'wedge_rising',
  direction: 'bearish',
  path: [
    [0, 8],
    [12, 28],
    [22, 16],
    [36, 36],
    [46, 28],
    [58, 42],
    [70, 22],
  ],
  lines: [
    [12, 28, 62, 44],
    [0, 8, 62, 34],
  ],
  breakout: [70, 22],
  note: 'Обе границы растут и сходятся; пробой вниз.',
};

const CHANNEL: PatternExample = {
  pattern: 'channel_ascending',
  direction: 'bullish',
  path: [
    [0, 6],
    [12, 24],
    [22, 14],
    [36, 34],
    [46, 24],
    [60, 44],
    [72, 52],
  ],
  lines: [
    [12, 24, 76, 50],
    [0, 6, 76, 32],
  ],
  breakout: [72, 52],
  note: 'Параллельные границы; выход за любую из них.',
};

const RANGE: PatternExample = {
  pattern: 'range_breakout',
  direction: 'bullish',
  path: [
    [0, 26],
    [10, 44],
    [22, 16],
    [34, 44],
    [46, 16],
    [58, 44],
    [72, 54],
  ],
  lines: [
    [10, 44, 84, 44],
    [22, 16, 84, 16],
  ],
  breakout: [72, 54],
  note: 'Горизонтальный диапазон; закрытие за границей с запасом в ATR.',
};

export const PATTERN_EXAMPLES: PatternExample[] = [
  HS,
  {
    pattern: 'inverse_head_shoulders',
    direction: 'bullish',
    path: flip(HS.path),
    lines: flipLines(HS.lines),
    breakout: [76, 52],
    note: 'Зеркальная фигура; пробой шеи вверх.',
  },
  DOUBLE_TOP,
  {
    pattern: 'double_bottom',
    direction: 'bullish',
    path: flip(DOUBLE_TOP.path),
    lines: flipLines(DOUBLE_TOP.lines),
    breakout: [64, 52],
    note: 'Два равных дна; пробой вершины между ними вверх.',
  },
  TRIPLE_TOP,
  TRIANGLE_ASC,
  {
    pattern: 'triangle_descending',
    direction: 'bearish',
    path: flip(TRIANGLE_ASC.path),
    lines: flipLines(TRIANGLE_ASC.lines),
    breakout: [74, 8],
    note: 'Плоская поддержка и падающее сопротивление; пробой вниз.',
  },
  TRIANGLE_SYM,
  WEDGE_RISING,
  {
    pattern: 'wedge_falling',
    direction: 'bullish',
    path: flip(WEDGE_RISING.path),
    lines: flipLines(WEDGE_RISING.lines),
    breakout: [70, 38],
    note: 'Обе границы падают и сходятся; пробой вверх.',
  },
  CHANNEL,
  RANGE,
];
