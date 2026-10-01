import createClient, { type Client } from 'openapi-fetch';

import type { components, paths } from './schema';

export type { components, operations, paths } from './schema';

export type ApiClient = Client<paths>;

/** Типизированный клиент REST API по схеме OpenAPI (генерируется из `apps/api`). */
export function createApiClient(baseUrl = ''): ApiClient {
  return createClient<paths>({ baseUrl });
}

export type Job = components['schemas']['JobOut'];
export type Root = components['schemas']['RootOut'];
export type Contract = components['schemas']['ContractOut'];
export type Candle = components['schemas']['CandleOut'];
export type CandlesResponse = components['schemas']['CandlesOut'];
export type RootIn = components['schemas']['RootIn'];
export type RootPatch = components['schemas']['RootPatch'];
export type ContractIn = components['schemas']['ContractIn'];
export type ContractPatch = components['schemas']['ContractPatch'];
export type CalendarDetail = components['schemas']['CalendarDetail'];
export type CalendarDays = components['schemas']['CalendarDays'];
export type IssContractIn = components['schemas']['IssContractIn'];
export type FileMapping = components['schemas']['FileMapping'];
export type FilePreview = components['schemas']['FilePreview'];
export type ImportRecord = components['schemas']['ImportOut'];
export type ImportPreset = components['schemas']['PresetOut'];
export type ImportedFile = components['schemas']['FileOut'];
export type Conflict = components['schemas']['ConflictOut'];
export type Roll = components['schemas']['RollOut'];
export type StepPrice = components['schemas']['StepPriceOut'];
export type IndicatorInfo = components['schemas']['IndicatorInfo'];
export type IndicatorPoint = components['schemas']['IndicatorPoint'];
export type IndicatorValues = components['schemas']['IndicatorValuesOut'];
export type ChartProfile = components['schemas']['ProfileOut'];
export type ProfileConfig = components['schemas']['ProfileConfig'];
export type Timeframe = '1m' | '15m' | '1h' | '4h' | '1d' | '1w';
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
export type AnalogueMatch = components['schemas']['MatchOut'];
