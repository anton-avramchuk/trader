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
export type Timeframe = '1m' | '15m' | '1h' | '4h' | '1d' | '1w';
