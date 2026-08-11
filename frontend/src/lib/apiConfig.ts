// Keep browser requests same-origin in local demo mode. Vite proxies this
// prefix to FastAPI, avoiding Private Network Access/client blocking of direct
// browser calls to 127.0.0.1:8000.
const LOCAL_API_BASE_URL = '/backend';

function normalizeBaseUrl(value: string | undefined): string {
  return (value?.trim() || LOCAL_API_BASE_URL).replace(/\/+$/, '');
}

export const API_BASE_URL = normalizeBaseUrl(
  import.meta.env.VITE_API_BASE_URL as string | undefined
);

export const DOCUMENTS_API_BASE_URL = normalizeBaseUrl(
  (import.meta.env.VITE_DOCUMENTS_API_BASE_URL as string | undefined) ??
    API_BASE_URL
);
