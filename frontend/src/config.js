// Single source of truth for the backend address.
//
// Local dev (default): same host on :8000 — works on localhost and LAN.
// Cloud: set VITE_BACKEND_URL at build time (Vercel → Project → Settings →
// Environment Variables), e.g. https://nexus-api.up.railway.app
const fromEnv = import.meta.env.VITE_BACKEND_URL;

export const BACKEND_URL =
  (fromEnv && fromEnv.replace(/\/+$/, '')) || `http://${window.location.hostname}:8000`;

// ws:// or wss:// derived from the backend URL (wss when the API is https)
export const WS_BASE = BACKEND_URL.replace(/^http/, 'ws');
