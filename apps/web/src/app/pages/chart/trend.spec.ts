import type { EngineEvent } from '@trader/api-client';
import { LAYER_HINTS, LAYERS } from './structure';
import { describeTrend, trendInfo } from './trend';

const event = (seq: number, payload: Record<string, unknown>): EngineEvent => ({
  seq,
  kind: 'trend',
  status: 'confirmed',
  payload,
  detected_at: '2026-09-28T04:00:00Z',
  confirmed_at: '2026-09-28T04:00:00Z',
  available_at: '2026-09-28T04:00:00Z',
  revises: null,
});

describe('trendInfo', () => {
  it('берёт последнее событие тренда', () => {
    const info = trendInfo([
      event(0, {
        state: 'range',
        strength: 10,
        bucket: 'none',
        since: 'a',
        efficiency: 0.1,
      }),
      event(1, {
        state: 'uptrend',
        strength: 90,
        bucket: 'strong',
        since: '2026-09-20T00:00:00Z',
        efficiency: 0.55,
      }),
    ]);

    expect(info).toEqual({
      state: 'uptrend',
      strength: 90,
      bucket: 'strong',
      since: '2026-09-20T00:00:00Z',
      efficiency: 0.55,
    });
    expect(describeTrend(info as never)).toBe('Восходящий, сильный');
  });

  it('боковик без корзины силы; пусто — null', () => {
    const info = trendInfo([
      event(0, {
        state: 'range',
        strength: 0,
        bucket: 'none',
        since: 'a',
        efficiency: 0,
      }),
    ]);

    expect(describeTrend(info as never)).toBe('Боковик');
    expect(trendInfo([])).toBeNull();
  });
});

describe('подсказки слоёв', () => {
  it('есть у каждого слоя', () => {
    for (const layer of LAYERS) {
      expect(LAYER_HINTS[layer].length).toBeGreaterThan(30);
    }
  });
});
