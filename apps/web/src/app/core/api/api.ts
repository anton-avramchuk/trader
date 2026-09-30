import { inject, Injectable, InjectionToken } from '@angular/core';
import { type ApiClient, createApiClient } from '@trader/api-client';

const NO_CONTENT = 204;
const SERVER_ERROR = 500;

/** Базовый адрес REST API: dev-сервер проксирует `/api` на FastAPI. */
export const API_BASE_URL = new InjectionToken<string>('API_BASE_URL', {
  providedIn: 'root',
  factory: () => '/api',
});

/** Сам клиент — токеном, чтобы в тестах подменять. */
export const API_CLIENT = new InjectionToken<ApiClient>('API_CLIENT', {
  providedIn: 'root',
  factory: () => createApiClient(inject(API_BASE_URL)),
});

/** Ошибка ответа API с понятным текстом для пользователя. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }

  get isNetwork(): boolean {
    return this.status === 0;
  }
}

interface Result<T> {
  data?: T;
  error?: unknown;
  response: { status: number };
}

interface ValidationItem {
  loc?: unknown[];
  msg?: string;
}

/** Читаемое сообщение из тела ошибки FastAPI (`detail`: строка или список ошибок валидации). */
export function describeError(error: unknown, status: number): string {
  if (error && typeof error === 'object' && 'detail' in error) {
    const detail = (error as { detail: unknown }).detail;
    if (typeof detail === 'string') {
      return detail;
    }
    if (Array.isArray(detail)) {
      return (detail as ValidationItem[])
        .map((item) => {
          const field = (item.loc ?? []).slice(1).join('.');
          return field ? `${field}: ${item.msg}` : (item.msg ?? '');
        })
        .filter(Boolean)
        .join('; ');
    }
  }
  if (status >= SERVER_ERROR) {
    return 'Ошибка сервера. Попробуйте позже.';
  }
  return `Запрос не выполнен (код ${status})`;
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  /** Типизированный клиент (`client.GET('/roots')` и т. д.). */
  readonly client = inject(API_CLIENT);

  /**
   * Выполняет запрос и возвращает данные; при ошибке бросает `ApiError`
   * (её показывает глобальный обработчик).
   */
  async call<T>(request: Promise<Result<T>>): Promise<T> {
    let result: Result<T>;
    try {
      result = await request;
    } catch {
      throw new ApiError(0, 'Нет связи с сервером');
    }
    if (result.error !== undefined) {
      throw new ApiError(
        result.response.status,
        describeError(result.error, result.response.status),
      );
    }
    if (result.data === undefined && result.response.status !== NO_CONTENT) {
      throw new ApiError(result.response.status, 'Пустой ответ сервера');
    }
    return result.data as T;
  }
}
