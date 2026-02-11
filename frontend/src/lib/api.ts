/**
 * Dynamic API URL utility
 *
 * This solves the problem of accessing the app from different devices on the network.
 * Instead of hardcoding localhost or a specific IP, we derive the API URL from
 * the browser's current location.
 *
 * How it works:
 * - If accessed via localhost:3000 -> API is localhost:8000
 * - If accessed via 192.168.1.5:3000 -> API is 192.168.1.5:8000
 * - If accessed via myserver.local:3000 -> API is myserver.local:8000
 */

const API_PORT = process.env.NEXT_PUBLIC_API_PORT || '8000';

export function getApiUrl(): string {
  // Server-side rendering: use env var or default
  if (typeof window === 'undefined') {
    return process.env.NEXT_PUBLIC_API_URL || `http://localhost:${API_PORT}`;
  }

  // Client-side: derive from current browser location
  const { protocol, hostname } = window.location;
  return `${protocol}//${hostname}:${API_PORT}`;
}
