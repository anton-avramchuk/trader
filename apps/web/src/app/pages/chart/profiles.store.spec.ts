import { TestBed } from '@angular/core/testing';
import type { ChartProfile, IndicatorInfo } from '@trader/api-client';
import { API_CLIENT } from '../../core/api/api';
import { IndicatorsStore } from './indicators.store';
import { ProfilesStore } from './profiles.store';
import { StructureStore } from './structure.store';

const ok = <T>(data: T) => Promise.resolve({ data, response: { status: 200 } });

const EMA: IndicatorInfo = {
  name: 'ema',
  title: 'EMA — экспоненциальная скользящая средняя',
  version: 1,
  pane: 'price',
  outputs: ['value'],
  warmup_bars: 20,
  params_schema: { properties: {} },
  defaults: {},
};

function profile(
  id: number,
  name: string,
  extra: Partial<ChartProfile> = {},
): ChartProfile {
  return {
    id,
    name,
    root_id: null,
    config: {
      chart_timeframe: '15m',
      indicators: [
        { name: 'ema', params: { period: 200 }, source_timeframe: '1h' },
      ],
      layers: {},
      style: {},
    },
    created_at: '2026-09-30T00:00:00Z',
    updated_at: '2026-09-30T00:00:00Z',
    last_used_at: null,
    ...extra,
  };
}

function setup(initial: ChartProfile[], withStructure = false) {
  let stored = [...initial];
  const client = {
    GET: vi.fn((path: string) => {
      if (path === '/chart-profiles') {
        return ok(stored);
      }
      // значения индикаторов — пусто, важно только, что запрос не падает
      return ok({});
    }),
    POST: vi.fn((path: string, request?: { body?: { name: string } }) => {
      if (path === '/chart-profiles') {
        const created = profile(99, request?.body?.name ?? '?');
        stored = [...stored, created];
        return ok(created);
      }
      return ok(stored[0]);
    }),
    PATCH: vi.fn(() => ok(stored[0])),
    DELETE: vi.fn(() => Promise.resolve({ response: { status: 204 } })),
  };
  TestBed.configureTestingModule({
    providers: [
      IndicatorsStore,
      ProfilesStore,
      ...(withStructure ? [StructureStore] : []),
      { provide: API_CLIENT, useValue: client },
    ],
  });
  const indicators = TestBed.inject(IndicatorsStore);
  indicators.catalog.set([EMA]);
  return {
    client,
    indicators,
    store: TestBed.inject(ProfilesStore),
    setStored: (v: ChartProfile[]) => (stored = v),
  };
}

type Call = [
  string,
  { params?: { query?: unknown; path?: unknown }; body?: unknown }?,
];
const calls = (mock: ReturnType<typeof vi.fn>) =>
  mock.mock.calls as unknown as Call[];

describe('ProfilesStore', () => {
  it('загружает профили root и глобальные', async () => {
    const { store, client } = setup([profile(1, 'A')]);

    await store.load(7);

    expect(store.profiles().map((p) => p.name)).toEqual(['A']);
    expect(calls(client.GET)[0][1]?.params?.query).toEqual({ root_id: 7 });
  });

  it('при первой загрузке применяет последний использованный профиль', async () => {
    const { store, indicators, client } = setup([
      profile(1, 'A'),
      profile(2, 'B', { last_used_at: '2026-09-29T00:00:00Z' }),
    ]);
    const timeframes: string[] = [];

    await store.load(7, (tf) => timeframes.push(tf));

    expect(store.selectedId()).toBe(2);
    expect(indicators.active()).toHaveLength(1);
    expect(indicators.active()[0]).toMatchObject({
      name: 'ema',
      params: { period: 200 },
      sourceTimeframe: '1h',
      title: EMA.title,
    });
    expect(timeframes).toEqual(['15m']);
    const use = calls(client.POST).find(([path]) => path.endsWith('/use'));
    expect(use?.[1]?.params?.path).toEqual({ profile_id: 2 });
  });

  it('не затирает уже выбранные индикаторы и не применяет профиль повторно', async () => {
    const { store, indicators } = setup([
      profile(2, 'B', { last_used_at: '2026-09-29T00:00:00Z' }),
    ]);
    await indicators.add(EMA, { period: 5 }, '15m');

    await store.load(7);
    await store.load(7);

    expect(indicators.active()).toHaveLength(1);
    expect(indicators.active()[0].params).toEqual({ period: 5 });
    expect(store.selectedId()).toBeNull();
  });

  it('смена root снова разрешает автоприменение', async () => {
    const { store, indicators } = setup([
      profile(2, 'B', { last_used_at: '2026-09-29T00:00:00Z' }),
    ]);
    await store.load(7);
    indicators.active.set([]);

    await store.load(8);

    expect(store.selectedId()).toBe(2);
  });

  it('глобальный выбранный профиль остаётся при смене root, чужой — сбрасывается', async () => {
    const { store, setStored } = setup([
      profile(1, 'Глобальный'),
      profile(2, 'Для NG', { root_id: 7 }),
    ]);
    await store.load(7);
    await store.apply(1);

    setStored([profile(1, 'Глобальный')]);
    await store.load(8);
    expect(store.selectedId()).toBe(1);

    await store.apply(1);
    setStored([profile(3, 'Другой', { root_id: 8 })]);
    await store.load(9);
    expect(store.selectedId()).toBeNull();
  });

  it('сохранение нового профиля берёт текущие индикаторы и область', async () => {
    const { store, indicators, client } = setup([]);
    await store.load(7);
    await indicators.add(EMA, { period: 50 }, '1h');

    await store.saveNew('  Мой  ', 'root', '15m');

    const created = calls(client.POST).find(
      ([path]) => path === '/chart-profiles',
    );
    expect(created?.[1]?.body).toEqual({
      name: 'Мой',
      root_id: 7,
      config: {
        chart_timeframe: '15m',
        indicators: [
          { name: 'ema', params: { period: 50 }, source_timeframe: '1h' },
        ],
        layers: {},
        style: {},
      },
    });
    expect(store.selectedId()).toBe(99);
  });

  it('глобальный профиль сохраняется без root', async () => {
    const { store, client } = setup([]);
    await store.load(7);

    await store.saveNew('Общий', 'global', '1h');

    const created = calls(client.POST).find(
      ([path]) => path === '/chart-profiles',
    );
    expect(
      (created?.[1]?.body as { root_id: number | null }).root_id,
    ).toBeNull();
  });

  it('сохранение изменений, переименование и удаление идут в выбранный профиль', async () => {
    const { store, client, indicators } = setup([profile(1, 'A')]);
    await store.load(7);
    await store.apply(1);
    await indicators.add(EMA, {}, '15m');

    await store.saveCurrent('4h');
    await store.rename('  Новое ');
    await store.remove();

    const patches = calls(client.PATCH);
    expect(patches[0][1]?.params?.path).toEqual({ profile_id: 1 });
    expect(
      (patches[0][1]?.body as { config: { chart_timeframe: string } }).config
        .chart_timeframe,
    ).toBe('4h');
    expect(patches[1][1]?.body).toEqual({ name: 'Новое' });
    expect(calls(client.DELETE)).toHaveLength(1);
    expect(store.selectedId()).toBeNull();
  });

  it('без выбранного профиля правки и удаление ничего не отправляют', async () => {
    const { store, client } = setup([]);

    await store.saveCurrent('15m');
    await store.rename('x');
    await store.remove();

    expect(client.PATCH).not.toHaveBeenCalled();
    expect(client.DELETE).not.toHaveBeenCalled();
  });

  describe('слои структуры', () => {
    it('в профиль сохраняются включённые слои, при применении — восстанавливаются', async () => {
      const { store, client } = setup(
        [
          profile(1, 'A', {
            config: {
              chart_timeframe: '15m',
              indicators: [],
              layers: { 'structure.levels': true, 'structure.pivot': false },
              style: {},
            },
          }),
        ],
        true,
      );
      const structure = TestBed.inject(StructureStore);
      await store.load(7);

      await store.apply(1);

      expect(structure.layers().levels).toBe(true);
      expect(structure.layers().pivot).toBe(false);
      expect(structure.layers().swings).toBe(false);
      await store.saveCurrent('15m');
      const patch = calls(client.PATCH).at(-1)?.[1]?.body as {
        config: { layers: Record<string, boolean> };
      };
      expect(patch.config.layers['structure.levels']).toBe(true);
      expect(Object.keys(patch.config.layers)).toHaveLength(8);
    });

    it('профиль без ключей структуры слои не трогает', async () => {
      const { store } = setup([profile(1, 'A')], true);
      const structure = TestBed.inject(StructureStore);
      await structure.setLayer('zones', true);
      await store.load(7);

      await store.apply(1);

      expect(structure.layers().zones).toBe(true);
    });
  });
});
