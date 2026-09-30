// Dev-сервер проксирует /api (REST и WebSocket) на FastAPI: без CORS и с тем же
// адресом в dev и в контейнере. Адрес API: переменная API_URL.
const target = process.env.API_URL || 'http://127.0.0.1:8000';

module.exports = {
  '/api': {
    target,
    changeOrigin: true,
    ws: true,
    pathRewrite: { '^/api': '' },
  },
};
