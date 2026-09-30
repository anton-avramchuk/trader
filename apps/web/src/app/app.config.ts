import {
  ApplicationConfig,
  ErrorHandler,
  provideBrowserGlobalErrorListeners,
} from '@angular/core';
import { provideRouter, withComponentInputBinding } from '@angular/router';
import { provideTaiga } from '@taiga-ui/core';
import { provideEventPlugins } from '@taiga-ui/event-plugins';
import { AppErrorHandler } from './core/api/error-handler';
import { appRoutes } from './app.routes';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideEventPlugins(),
    provideTaiga(),
    provideRouter(appRoutes, withComponentInputBinding()),
    { provide: ErrorHandler, useClass: AppErrorHandler },
  ],
};
