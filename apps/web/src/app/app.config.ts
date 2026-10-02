import {
  ApplicationConfig,
  ErrorHandler,
  provideBrowserGlobalErrorListeners,
  signal,
} from '@angular/core';
import { provideRouter, withComponentInputBinding } from '@angular/router';
import { provideTaiga } from '@taiga-ui/core';
import { provideEventPlugins } from '@taiga-ui/event-plugins';
import { TUI_RUSSIAN_LANGUAGE } from '@taiga-ui/i18n/languages/russian';
import { TUI_LANGUAGE } from '@taiga-ui/i18n/tokens';
import { AppErrorHandler } from './core/api/error-handler';
import { appRoutes } from './app.routes';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideEventPlugins(),
    provideTaiga(),
    { provide: TUI_LANGUAGE, useValue: signal(TUI_RUSSIAN_LANGUAGE) },
    provideRouter(appRoutes, withComponentInputBinding()),
    { provide: ErrorHandler, useClass: AppErrorHandler },
  ],
};
