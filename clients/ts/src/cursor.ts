/**
 * Keyset pagination cursor codec.
 *
 * Mirrors ExecutionStateMachine._encode_cursor / _decode_cursor in
 * plugins/onestep-sql/src/onestep_sql/_shared/execution/machine.py:1142-1178.
 *
 * Python's encoder is:
 *   base64url(json.dumps({"v":1,"created_at":dt.isoformat(),"id":str(uuid)}, separators=(",",":")))
 *   with trailing "=" padding stripped
 *
 * Two properties matter for interoperability:
 *
 *   1. Keys are NOT sorted (unlike the submission digest), and the JSON is
 *      tight-separated. The field order is v, created_at, id.
 *   2. Python's `_decode_cursor` re-encodes the parsed value and compares it to
 *      the inbound string. A cursor your client emits must therefore re-encode
 *      to the identical string, or every paginated request fails. That means
 *      the datetime text we emit must survive `datetime.fromisoformat` ->
 *      `.astimezone(utc)` -> `.isoformat()` unchanged.
 *
 * Rule (2) has a sharp consequence: we must send UTC datetimes whose
 * `isoformat()` round-trips exactly — microseconds present only when non-zero,
 * and the "+00:00" suffix (not "Z").
 */

/** Base64url-encode without padding (Python's rstrip("=")). */
function b64urlEncode(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let binary = '';
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/**
 * Decode base64url with strict validation, matching Python's
 * `base64.b64decode(padded, altchars=b"-_", validate=True)`.
 */
function b64urlDecode(value: string): string {
  if (value.length > 1024) throw new Error('invalid execution cursor');
  if (/\s/.test(value)) throw new Error('invalid execution cursor');
  const padded = value + '='.repeat((4 - (value.length % 4)) % 4);
  const normalized = padded.replace(/-/g, '+').replace(/_/g, '/');
  let binary: string;
  try {
    binary = atob(normalized);
  } catch {
    throw new Error('invalid execution cursor');
  }
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}

/**
 * Normalize an ISO-8601 datetime string to Python `isoformat()` form in UTC.
 *
 * `2026-09-17T18:07:00.123456+08:00` -> `2026-09-17T10:07:00.123456+00:00`
 * `2026-09-17T10:07:00Z`             -> `2026-09-17T10:07:00+00:00`
 *
 * Converting to UTC is not a stylistic choice: the server's `_decode_cursor`
 * re-encodes the parsed value after `astimezone(timezone.utc)` and requires the
 * result to equal the inbound string. A cursor carrying a non-UTC offset would
 * therefore be rejected by the server's own round-trip check. Emitting UTC is
 * the only form that survives.
 */
export function toPythonIsoformatUtc(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    throw new Error(`invalid datetime: ${iso}`);
  }
  // Preserve microsecond precision that JS Date would truncate.
  const micros = extractMicroseconds(iso);
  const pad = (n: number, w = 2) => String(n).padStart(w, '0');
  const base =
    `${date.getUTCFullYear()}-${pad(date.getUTCMonth() + 1)}-${pad(date.getUTCDate())}` +
    `T${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())}`;
  const frac = micros ? '.' + micros : '';
  return `${base}${frac}+00:00`;
}

/** Pull the fractional-seconds digits from an ISO string, if any. */
function extractMicroseconds(iso: string): string | null {
  const m = iso.match(/\.(\d+)/);
  if (!m) return null;
  const digits = m[1].replace(/0+$/, '');
  return digits.length === 0 ? null : digits.padEnd(6, '0').slice(0, 6);
}

/**
 * Encode a keyset cursor exactly as Python does.
 *
 * Field order (v, created_at, id) is significant; keys are not sorted.
 */
export function encodeCursor(createdAtIso: string, id: string): string {
  const created = toPythonIsoformatUtc(createdAtIso);
  const payload = `{"v":1,"created_at":${JSON.stringify(created)},"id":${JSON.stringify(id)}}`;
  return b64urlEncode(payload);
}

/** A decoded cursor. */
export interface DecodedCursor {
  createdAt: string;
  id: string;
}

/**
 * Decode and validate a cursor, including Python's round-trip equality check.
 */
export function decodeCursor(cursor: string): DecodedCursor {
  const raw = b64urlDecode(cursor);
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new Error('invalid execution cursor');
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    throw new Error('invalid execution cursor');
  }
  const obj = parsed as Record<string, unknown>;
  const keys = Object.keys(obj).sort();
  if (keys.length !== 3 || keys.join(',') !== 'created_at,id,v') {
    throw new Error('invalid execution cursor');
  }
  if (typeof obj.v !== 'number' || !Number.isInteger(obj.v)) {
    throw new Error('invalid execution cursor');
  }
  if (obj.v !== 1) throw new Error('unknown cursor version');
  if (typeof obj.created_at !== 'string' || typeof obj.id !== 'string') {
    throw new Error('invalid execution cursor');
  }
  // Round-trip equality, as Python enforces.
  const reencoded = encodeCursor(obj.created_at, obj.id);
  if (reencoded !== cursor) throw new Error('invalid execution cursor');
  return { createdAt: toPythonIsoformatUtc(obj.created_at), id: obj.id };
}
