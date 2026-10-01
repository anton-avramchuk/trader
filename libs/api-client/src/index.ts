import createClient, { type Client } from 'openapi-fetch';

import type { components, paths } from './schema';

export type { components, operations, paths } from './schema';

export type ApiClient = Client<paths>;

/** Типизированный клиент REST API по схеме OpenAPI (генерируется из `apps/api`). */
export function createApiClient(baseUrl = ''): ApiClient {
  return createClient<paths>({ baseUrl });
}

export type Job = components['schemas']['JobOut'];
export type Instrument = components['schemas']['InstrumentOut'];
export type InstrumentCoverage = components['schemas']['CoverageOut'];
export type ImporterTicker = components['schemas']['TickerOut'];
export type CandleLoad = components['schemas']['LoadOut'];
export type Candle = components['schemas']['CandleOut'];
export type CandlesResponse = components['schemas']['CandlesOut'];
export type IndicatorInfo = components['schemas']['IndicatorInfo'];
export type IndicatorPoint = components['schemas']['IndicatorPoint'];
export type IndicatorValues = components['schemas']['IndicatorValuesOut'];
export type ChartProfile = components['schemas']['ProfileOut'];
export type ProfileConfig = components['schemas']['ProfileConfig'];
export type Timeframe = '15m' | '1h' | '4h' | '1d' | '1w';
export type EngineRun = components['schemas']['EngineRunOut'];
export type EngineEvent = components['schemas']['EngineEventOut'];
export type LevelZones = components['schemas']['ZonesOut'];
export type LevelZone = components['schemas']['ZoneOut'];
export type FibGrid = components['schemas']['FibGridOut'];
export type OutcomeStats = components['schemas']['StatsOut'];
export type OutcomeBucket = components['schemas']['BucketOut'];
export type HorizonStats = components['schemas']['HorizonStatsOut'];
export type OccurrenceOutcomes = components['schemas']['OccurrenceDetailOut'];
export type Analogues = components['schemas']['AnaloguesOut'];
export type Forecast = components['schemas']['ForecastOut'];
export type MethodForecast = components['schemas']['MethodOut'];
export type HorizonForecast = components['schemas']['HorizonForecastOut'];
export type ForecastCalibration = components['schemas']['CalibrationOut'];
export type Backtest = components['schemas']['BacktestOut'];
export type BacktestCreate = components['schemas']['BacktestCreate'];
export type BacktestTrade = components['schemas']['TradeOut'];
export type BacktestWindow = components['schemas']['WindowOut'];
export type BacktestLogEntry = components['schemas']['LogOut'];
export type BacktestLock = components['schemas']['LockOut'];
export type AnalogueMatch = components['schemas']['MatchOut'];
