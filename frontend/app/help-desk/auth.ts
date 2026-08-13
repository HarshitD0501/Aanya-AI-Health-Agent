import { timingSafeEqual } from 'node:crypto';

/**
 * The gate for /help-desk.
 *
 * These rows hold health complaints and callback numbers, so the token stays a
 * server-side secret: it is never sent to the browser as JS, never put in a
 * NEXT_PUBLIC_ var, and never baked into a nav link. The nav bar therefore links
 * to a bare /help-desk, and the page itself asks for the token once and
 * remembers it in an httpOnly cookie.
 */
export const HELPDESK_COOKIE = 'aanya_helpdesk';

/** Twelve hours: long enough for a demo session, short enough to expire on its own. */
export const HELPDESK_COOKIE_MAX_AGE = 60 * 60 * 12;

/** The configured token, or undefined when the gate is switched off entirely. */
export function helpDeskToken(): string | undefined {
  return process.env.HELPDESK_TOKEN?.trim() || undefined;
}

/** Constant-time comparison, so the gate does not leak the token's prefix. */
export function tokenMatches(provided: string | undefined, expected: string): boolean {
  const a = Buffer.from(provided ?? '', 'utf8');
  const b = Buffer.from(expected, 'utf8');
  return a.length === b.length && timingSafeEqual(a, b);
}
