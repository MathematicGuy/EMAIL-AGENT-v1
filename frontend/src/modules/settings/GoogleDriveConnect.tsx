/**
 * GoogleDriveConnect.tsx
 *
 * Settings panel component for connecting / disconnecting Google Drive.
 * Single-user MVP: reads env-based connection status from the backend health
 * endpoint. The actual OAuth flow (for the multi-user phase) will redirect to
 * GET /api/v1/oauth/google/authorize.
 *
 * Design follows DESIGN.md conventions: Tailwind utility classes, consistent
 * with the rest of the settings UI.
 */

import { useCallback, useEffect, useState } from 'react';
import { API_BASE_URL } from '../../lib/apiConfig';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface DriveConnectorStatus {
  connected: boolean;
  enabled: boolean;
  /** Present when connected: masked credential indicator */
  account_hint?: string;
  error?: string;
}

type FetchState = 'idle' | 'loading' | 'success' | 'error';

// ---------------------------------------------------------------------------
// API helper
// ---------------------------------------------------------------------------

const DRIVE_STATUS_URL = `${API_BASE_URL}/api/v1/connectors/google-drive/status`;

async function fetchDriveStatus(): Promise<DriveConnectorStatus> {
  const res = await fetch(DRIVE_STATUS_URL, {
    headers: { 'X-Service-Identity': 'ui' },
  });
  if (!res.ok) {
    throw new Error(`Status fetch failed: ${res.status}`);
  }
  return res.json() as Promise<DriveConnectorStatus>;
}

// ---------------------------------------------------------------------------
// Icons (inline SVG — no extra dependency)
// ---------------------------------------------------------------------------

function DriveIcon({ className = 'w-5 h-5' }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 87.3 78"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <path
        d="M6.6 66.85l3.85 6.65c.8 1.4 1.95 2.5 3.3 3.3l13.75-23.8H0a15.92 15.92 0 0 0 2.1 8z"
        fill="#0066da"
      />
      <path
        d="M43.65 25L29.9 1.2C28.55 2 27.4 3.1 26.6 4.5L1.1 49.1A15.92 15.92 0 0 0 0 53H27.5z"
        fill="#00ac47"
      />
      <path
        d="M73.55 76.8c1.35-.8 2.5-1.9 3.3-3.3l1.6-2.75 7.65-13.25c.8-1.4 1.2-2.95 1.2-4.5H59.8l5.85 11.5z"
        fill="#ea4335"
      />
      <path
        d="M43.65 25L57.4 1.2C56.05.45 54.5 0 52.85 0H34.45c-1.65 0-3.2.45-4.55 1.2z"
        fill="#00832d"
      />
      <path
        d="M59.8 53H27.5L13.75 76.8c1.35.75 2.9 1.2 4.55 1.2h50.7c1.65 0 3.2-.45 4.55-1.2z"
        fill="#2684fc"
      />
      <path
        d="M73.4 26.5l-25.5-44.1C46.55 1 44.95.55 43.35.55S40.1 1 38.75 1.75L43.65 25H71.2c0-1.55-.4-3.1-1.1-4.5z"
        fill="#ffba00"
      />
    </svg>
  );
}

function CheckIcon({ className = 'w-4 h-4' }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
    </svg>
  );
}

function AlertIcon({ className = 'w-4 h-4' }: { className?: string }) {
  return (
    <svg className={className} fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
      />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function GoogleDriveConnect() {
  const [status, setStatus] = useState<DriveConnectorStatus | null>(null);
  const [fetchState, setFetchState] = useState<FetchState>('idle');
  const [fetchError, setFetchError] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    setFetchState('loading');
    setFetchError(null);
    try {
      const data = await fetchDriveStatus();
      setStatus(data);
      setFetchState('success');
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Unknown error';
      setFetchError(msg);
      setFetchState('error');
    }
  }, []);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  // -------------------------------------------------------------------------
  // Derived state
  // -------------------------------------------------------------------------

  const isConnected = status?.connected ?? false;
  const isEnabled = status?.enabled ?? false;
  const accountHint = status?.account_hint ?? null;

  // -------------------------------------------------------------------------
  // Render helpers
  // -------------------------------------------------------------------------

  function renderStatusBadge() {
    if (fetchState === 'loading') {
      return (
        <span className="inline-flex items-center gap-1.5 rounded-full bg-gray-100 px-2.5 py-0.5 text-xs font-medium text-gray-500">
          <span className="h-2 w-2 animate-pulse rounded-full bg-gray-400" />
          Checking…
        </span>
      );
    }

    if (!isEnabled) {
      return (
        <span
          id="gdrive-status-badge"
          className="inline-flex items-center gap-1.5 rounded-full bg-yellow-50 px-2.5 py-0.5 text-xs font-medium text-yellow-700"
        >
          <AlertIcon className="w-3 h-3" />
          Not configured
        </span>
      );
    }

    if (isConnected) {
      return (
        <span
          id="gdrive-status-badge"
          className="inline-flex items-center gap-1.5 rounded-full bg-green-50 px-2.5 py-0.5 text-xs font-medium text-green-700"
        >
          <CheckIcon className="w-3 h-3" />
          Connected{accountHint ? ` · ${accountHint}` : ''}
        </span>
      );
    }

    return (
      <span
        id="gdrive-status-badge"
        className="inline-flex items-center gap-1.5 rounded-full bg-red-50 px-2.5 py-0.5 text-xs font-medium text-red-600"
      >
        <AlertIcon className="w-3 h-3" />
        Not connected
      </span>
    );
  }

  function renderBody() {
    if (fetchState === 'error') {
      return (
        <div
          id="gdrive-error-banner"
          className="mt-3 rounded-md bg-red-50 p-3 text-sm text-red-700"
          role="alert"
        >
          <p className="font-medium">Could not reach backend</p>
          <p className="mt-0.5 text-xs text-red-500">{fetchError}</p>
          <button
            id="gdrive-retry-btn"
            onClick={() => void loadStatus()}
            className="mt-2 text-xs font-medium underline hover:no-underline"
          >
            Retry
          </button>
        </div>
      );
    }

    if (!isEnabled) {
      return (
        <div className="mt-3 rounded-md bg-yellow-50 p-3 text-sm text-yellow-800">
          <p className="font-medium">Google Drive is not enabled on this deployment.</p>
          <p className="mt-1 text-xs text-yellow-700">
            Set{' '}
            <code className="rounded bg-yellow-100 px-1 py-0.5 font-mono text-xs">
              GOOGLE_DRIVE_ENABLED=true
            </code>
            {', '}
            <code className="rounded bg-yellow-100 px-1 py-0.5 font-mono text-xs">
              GOOGLE_CLIENT_ID
            </code>
            {', '}
            <code className="rounded bg-yellow-100 px-1 py-0.5 font-mono text-xs">
              GOOGLE_CLIENT_SECRET
            </code>{' '}
            and{' '}
            <code className="rounded bg-yellow-100 px-1 py-0.5 font-mono text-xs">
              GOOGLE_DRIVE_REFRESH_TOKEN
            </code>{' '}
            in your backend <code className="font-mono text-xs">.env</code> file, then restart the
            server.
          </p>
        </div>
      );
    }

    if (isConnected) {
      return (
        <div className="mt-3 space-y-3">
          <p className="text-sm text-gray-600">
            Your Google Drive is connected. F-Cowork can search your files when you ask questions
            like{' '}
            <em>&ldquo;Tìm tài liệu hợp đồng trong Google Drive&rdquo;</em>.
          </p>
          <div className="rounded-md bg-green-50 p-3 text-sm text-green-800">
            <p className="flex items-center gap-1.5 font-medium">
              <CheckIcon className="w-4 h-4 text-green-600" />
              Active · read-only access
            </p>
            {accountHint && (
              <p className="mt-0.5 text-xs text-green-600">{accountHint}</p>
            )}
          </div>
          <p className="text-xs text-gray-400">
            To disconnect, remove the refresh token from your{' '}
            <code className="font-mono">.env</code> file and restart.
          </p>
        </div>
      );
    }

    // Enabled but not connected
    return (
      <div className="mt-3 space-y-3">
        <p className="text-sm text-gray-600">
          Connect your Google Drive to let F-Cowork search your files. A refresh token
          must be set in the backend environment.
        </p>
        <div className="rounded-md bg-red-50 p-3 text-sm text-red-800">
          <p className="font-medium">Credentials missing or invalid</p>
          <p className="mt-0.5 text-xs text-red-600">
            {status?.error ?? 'GOOGLE_DRIVE_REFRESH_TOKEN may be missing or expired.'}
          </p>
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // Root render
  // -------------------------------------------------------------------------

  return (
    <section
      id="gdrive-connect-panel"
      aria-labelledby="gdrive-heading"
      className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm"
    >
      {/* Header */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-white shadow ring-1 ring-gray-200">
            <DriveIcon className="h-5 w-5" />
          </div>
          <div>
            <h3 id="gdrive-heading" className="text-sm font-semibold text-gray-900">
              Google Drive
            </h3>
            <p className="text-xs text-gray-500">Read-only file search</p>
          </div>
        </div>
        {renderStatusBadge()}
      </div>

      {/* Body */}
      {renderBody()}
    </section>
  );
}

export default GoogleDriveConnect;
