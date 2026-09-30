import { ErrorHandler, inject, Injectable, Injector } from '@angular/core';
import { TuiAlertService } from '@taiga-ui/core';
import { ApiError } from './api';

/** Ошибки API показываются уведомлением, остальные — ещё и в консоль. */
@Injectable()
export class AppErrorHandler implements ErrorHandler {
  // Лениво: ErrorHandler создаётся при старте приложения, раньше корня Taiga.
  private readonly injector = inject(Injector);

  handleError(error: unknown): void {
    const cause = error instanceof Error ? error.cause : undefined;
    const apiError = [error, cause].find((e) => e instanceof ApiError);
    if (apiError) {
      this.injector
        .get(TuiAlertService)
        .open(apiError.message, {
          label: apiError.isNetwork ? 'Нет связи' : 'Ошибка запроса',
          appearance: 'negative',
        })
        .subscribe();
      return;
    }
    console.error(error);
    this.injector
      .get(TuiAlertService)
      .open('Что-то пошло не так. Подробности в консоли.', {
        label: 'Ошибка',
        appearance: 'negative',
      })
      .subscribe();
  }
}
