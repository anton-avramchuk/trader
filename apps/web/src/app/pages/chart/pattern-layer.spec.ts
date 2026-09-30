import type { EngineEvent } from '@trader/api-client';
import {
  chartPatterns,
  type PatternInfo,
  patternInfos,
  patternMarkers,
  patternSegments,
  shortName,
} from './pattern-layer';
import { PATTERN_EXAMPLES } from './pattern-examples';

const sec = (iso: string) => Date.parse(iso) / 1000;

interface Spec {
  seq: number;
  id: number;
  state: 'candidate' | 'confirmed' | 'invalidated';
  at: string;
  pattern?: string;
  direction?: string;
  reason?: string;
  target?: number | null;
  end?: string;
}

function event(spec: Spec): EngineEvent {
  const status = {
    candidate: 'detected',
    confirmed: 'confirmed',
    invalidated: 'invalidated',
  }[spec.state] as EngineEvent['status'];
  return {
    seq: spec.seq,
    kind: 'pattern',
    status,
    payload: {
      id: spec.id,
      pattern: spec.pattern ?? 'double_top',
      direction: spec.direction ?? 'bearish',
      state: spec.state,
      reason: spec.reason,
      start: '2026-09-28T04:00:00Z',
      end: spec.end ?? '2026-09-28T06:00:00Z',
      points: [
        { role: 'top1', price: 120, ts: '2026-09-28T04:00:00Z', index: 1 },
        { role: 'valley', price: 100, ts: '2026-09-28T05:00:00Z', index: 5 },
        {
          role: 'top2',
          price: 120,
          ts: spec.end ?? '2026-09-28T06:00:00Z',
          index: 9,
        },
      ],
      line: {
        p1: 100,
        p2: 100,
        t1: '2026-09-28T05:00:00Z',
        t2: '2026-09-28T06:00:00Z',
      },
      height: 20,
      target: spec.target ?? null,
      features: { width_bars: 8 },
      quality: { score: 61.5, components: { precision: 1, symmetry: 0.8 } },
    },
    detected_at: spec.at,
    confirmed_at: spec.state === 'confirmed' ? spec.at : null,
    available_at: spec.at,
    revises: spec.seq === 0 ? null : spec.seq - 1,
  };
}

const CHAIN = [
  event({ seq: 0, id: 1, state: 'candidate', at: '2026-09-28T06:15:00Z' }),
  event({
    seq: 1,
    id: 1,
    state: 'confirmed',
    at: '2026-09-28T07:00:00Z',
    target: 80,
  }),
];

describe('patternInfos', () => {
  it('сводит цепочку к последнему состоянию и помнит время обнаружения и подтверждения', () => {
    const [info] = patternInfos('double_triple', CHAIN);

    expect(info).toMatchObject({
      key: 'double_triple:1',
      pattern: 'double_top',
      state: 'confirmed',
      target: 80,
      detectedAt: '2026-09-28T06:15:00Z',
      confirmedAt: '2026-09-28T07:00:00Z',
      score: 61.5,
    });
    expect(info?.points.map((p) => p.role)).toEqual(['top1', 'valley', 'top2']);
  });

  it('несколько вхождений и причина отмены', () => {
    const infos = patternInfos('head_shoulders', [
      ...CHAIN,
      event({ seq: 2, id: 2, state: 'candidate', at: '2026-09-28T08:00:00Z' }),
      event({
        seq: 3,
        id: 2,
        state: 'invalidated',
        at: '2026-09-28T09:00:00Z',
        reason: 'broken',
      }),
    ]);

    expect(infos.map((i) => [i.id, i.state, i.reason])).toEqual([
      [1, 'confirmed', null],
      [2, 'invalidated', 'broken'],
    ]);
  });

  it('чужие события пропускаются', () => {
    const other = { ...CHAIN[0], kind: 'swing' } as EngineEvent;

    expect(patternInfos('x', [other])).toEqual([]);
  });
});

describe('chartPatterns', () => {
  const infos = [
    event({
      seq: 0,
      id: 1,
      state: 'candidate',
      at: 'x',
      end: '2026-09-28T05:00:00Z',
    }),
    event({
      seq: 1,
      id: 2,
      state: 'confirmed',
      at: '2026-09-28T07:00:00Z',
      end: '2026-09-28T06:00:00Z',
      target: 80,
    }),
    event({
      seq: 2,
      id: 3,
      state: 'invalidated',
      at: '2026-09-28T08:00:00Z',
      end: '2026-09-28T07:00:00Z',
      reason: 'expired',
    }),
  ].flatMap((e) => patternInfos('t', [e]));

  it('новые первыми, отменённые только по просьбе', () => {
    expect(chartPatterns(infos, false).map((i) => i.id)).toEqual([2, 1]);
    expect(chartPatterns(infos, true).map((i) => i.id)).toEqual([3, 2, 1]);
  });

  it('ограничение по числу, но выбранное остаётся', () => {
    expect(chartPatterns(infos, true, null, 1).map((i) => i.id)).toEqual([3]);
    expect(chartPatterns(infos, true, 't:1', 1).map((i) => i.id)).toEqual([
      1, 3,
    ]);
  });
});

describe('отрисовка', () => {
  const [confirmed] = patternInfos('double_triple', CHAIN) as [PatternInfo];
  const [candidate] = patternInfos('double_triple', [
    CHAIN[0] as EngineEvent,
  ]) as [PatternInfo];

  it('контур, шея и цель для подтверждённого; кандидат пунктиром и без цели', () => {
    const segments = patternSegments([confirmed]);

    expect(segments).toHaveLength(4); // 2 звена контура + шея + цель
    expect(segments[0]).toMatchObject({
      dashed: false,
      time1: sec('2026-09-28T04:00:00Z'),
    });
    const target = segments.at(-1);
    expect(target).toMatchObject({ price1: 80, time2: null, dashed: true });
    expect(target?.time1).toBe(sec('2026-09-28T07:00:00Z'));
    expect(target?.label).toBe('цель 2Top');

    const pending = patternSegments([candidate]);
    expect(pending).toHaveLength(3);
    expect(pending[0]?.dashed).toBe(true);
  });

  it('выбранное рисуется толще; отменённое — серым', () => {
    const cancelled = patternInfos('t', [
      event({ seq: 0, id: 9, state: 'invalidated', at: 'x', reason: 'broken' }),
    ])[0] as PatternInfo;

    const selected = patternSegments([confirmed], 'double_triple:1');
    const grey = patternSegments([cancelled]);

    expect(selected[0]?.width).toBe(4);
    expect(new Set(grey.map((s) => s.color)).size).toBe(1);
    expect(grey[0]?.color).toBe('#9e9e9e');
  });

  it('метка на последней точке: стрелка по направлению, «?» у кандидата', () => {
    const [bearish] = patternMarkers([confirmed]);
    const [pending] = patternMarkers([candidate]);
    const bullish = patternInfos('t', [
      event({
        seq: 0,
        id: 5,
        state: 'confirmed',
        at: 'x',
        direction: 'bullish',
        pattern: 'double_bottom',
      }),
    ]);

    expect(bearish).toMatchObject({
      position: 'aboveBar',
      shape: 'arrowDown',
      text: '2Top',
    });
    expect(pending?.text).toBe('2Top?');
    expect(patternMarkers(bullish)[0]).toMatchObject({
      position: 'belowBar',
      shape: 'arrowUp',
      text: '2Bot',
    });
  });
});

describe('примеры', () => {
  it('у каждого паттерна из движков есть схема с направлением и подписью', () => {
    const names = PATTERN_EXAMPLES.map((e) => e.pattern);

    expect(new Set(names).size).toBe(names.length);
    for (const example of PATTERN_EXAMPLES) {
      expect(example.path.length).toBeGreaterThanOrEqual(5);
      expect(example.lines.length).toBeGreaterThan(0);
      expect(shortName(example.pattern)).not.toBe(example.pattern);
    }
    expect(names).toEqual(
      expect.arrayContaining([
        'head_shoulders',
        'inverse_head_shoulders',
        'double_top',
        'double_bottom',
        'triple_top',
        'triangle_ascending',
        'triangle_descending',
        'triangle_symmetric',
        'wedge_rising',
        'wedge_falling',
        'channel_ascending',
        'range_breakout',
      ]),
    );
  });
});
