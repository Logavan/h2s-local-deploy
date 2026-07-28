// Web framework configuration.
//
// `api.baseUrl` is used by browser fetch() calls to reach the backend.
//
// Production deployments (behind a reverse proxy with TLS):
//   - Leave NEXT_PUBLIC_API_BASE_URL empty - the browser makes same-origin
//     requests to `/api/...`, which the reverse proxy routes to the backend.
//   - One frontend image works across environments (dev/staging/prod).
//
// Direct-to-backend deployments (bypassing the reverse proxy):
//   - Set NEXT_PUBLIC_API_BASE_URL to the public backend URL at BUILD time:
//       NEXT_PUBLIC_API_BASE_URL=https://hanacv2sql-api.client.local \
//         docker build -t hanacv2sql-frontend ./frontend
//   - The value is inlined into the JS bundle at build - changing it after
//     build requires rebuilding.
//
// Default: empty string = same-origin.

export const config = {
  api: {
    baseUrl: process.env.NEXT_PUBLIC_API_BASE_URL || '',
  },
};