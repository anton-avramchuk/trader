# api-client

Типизированный TS-клиент REST API. Схема `openapi.json` и типы `src/schema.ts` **генерируются** из приложения `apps/api` и лежат в репозитории:

```bash
npx nx run api-client:generate   # export-schema (python) → openapi-typescript
npx nx run api-client:test       # падает, если сгенерированное не совпадает с закоммиченным
```

Использование: `import { createApiClient } from '@trader/api-client'` — `client.GET('/roots')`, `client.POST('/jobs', { body })`; типы путей и тел берутся из схемы.
