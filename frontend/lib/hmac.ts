// frontend/lib/hmac.ts
// Frontend HMAC signing helper. Every request that mutates state on the
// backend must carry the three X-H2S-* headers. The backend rejects the
// request (401) if any are missing or invalid.
//
// Key handling: the HMAC key is fetched once from `/api/hmac/key` after the
// license gate has succeeded. The key is held in memory only — never written
// to localStorage or sessionStorage (XSS extraction resistance).

import { config } from "./config"

const KEY_CACHE: { key: string | null; fetchedAt: number } = {
  key: null,
  fetchedAt: 0,
};
const KEY_TTL_MS = 5 * 60 * 1000; // re-fetch every 5 minutes

async function getSigningKey(): Promise<string> {
  const now = Date.now();
  if (KEY_CACHE.key && now - KEY_CACHE.fetchedAt < KEY_TTL_MS) {
    return KEY_CACHE.key;
  }
  // Use the same baseUrl as the rest of the frontend so the key fetch stays
  // same-origin (Next.js rewrites proxy /api/* → backend in dev). Going
  // cross-origin here trips CORS preflights against H2S_ALLOWED_ORIGINS.
  const apiBase = config.api.baseUrl;
  const res = await fetch(`${apiBase}/api/hmac/key`, {
    credentials: "include",
  });
  if (!res.ok) {
    throw new Error(`Failed to fetch HMAC key: ${res.status}`);
  }
  const data = (await res.json()) as { key: string };
  KEY_CACHE.key = data.key;
  KEY_CACHE.fetchedAt = now;
  return data.key;
}

function base64ToBytes(b64: string): Uint8Array {
  const binary = atob(b64);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    out[i] = binary.charCodeAt(i);
  }
  return out;
}

function bytesToBase64(bytes: ArrayBuffer): string {
  let bin = "";
  const view = new Uint8Array(bytes);
  for (let i = 0; i < view.length; i++) {
    bin += String.fromCharCode(view[i]);
  }
  return btoa(bin);
}

async function sha256Hex(text: string): Promise<string> {
  const buf = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", buf);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function hmacSha256Base64(
  key: Uint8Array,
  message: string,
): Promise<string> {
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    key as BufferSource,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign(
    "HMAC",
    cryptoKey,
    new TextEncoder().encode(message) as BufferSource,
  );
  return bytesToBase64(sig);
}

/**
 * Build the three HMAC headers for a given request.
 * Returns headers that should be merged into the fetch() options.
 */
export async function signRequest(
  method: string,
  route: string,
  body: string | null = null,
): Promise<Record<string, string>> {
  const keyB64 = await getSigningKey();
  const key = base64ToBytes(keyB64);

  const now = new Date();
  const timestamp = now.toISOString().replace(/\.\d+Z$/, "Z");
  const nonce = crypto
    .getRandomValues(new Uint8Array(16))
    .reduce((acc, b) => acc + b.toString(16).padStart(2, "0"), "");

  const bodyHash = await sha256Hex(body || "");
  const canonical = `${timestamp}\n${nonce}\n${method.toUpperCase()}\n${route}\n${bodyHash}`;
  const signature = await hmacSha256Base64(key, canonical);

  return {
    "X-H2S-Timestamp": timestamp,
    "X-H2S-Nonce": nonce,
    "X-H2S-Signature": signature,
  };
}

/**
 * Reset the cached key — call this on logout / license change.
 */
export function clearSigningKey(): void {
  KEY_CACHE.key = null;
  KEY_CACHE.fetchedAt = 0;
}