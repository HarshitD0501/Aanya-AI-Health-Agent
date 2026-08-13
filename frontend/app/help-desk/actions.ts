'use server';

import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { HELPDESK_COOKIE, HELPDESK_COOKIE_MAX_AGE, helpDeskToken, tokenMatches } from './auth';

export interface UnlockState {
  error?: string;
}

/**
 * Verify the typed token and remember it, so the nav-bar link works from then on.
 *
 * A Server Action rather than a query string on purpose: ?token=… would sit in
 * the address bar, in browser history, and on screen during a screen recording.
 * The cookie is httpOnly, so nothing on the page can read it back out either.
 */
export async function unlockHelpDesk(
  _previous: UnlockState,
  formData: FormData
): Promise<UnlockState> {
  const expected = helpDeskToken();
  if (!expected) {
    // The gate is off (no HELPDESK_TOKEN set); the page is already readable.
    redirect('/help-desk');
  }

  const provided = String(formData.get('token') ?? '');
  if (!tokenMatches(provided, expected)) {
    return { error: 'That token does not match HELPDESK_TOKEN in frontend/.env.local.' };
  }

  (await cookies()).set(HELPDESK_COOKIE, expected, {
    httpOnly: true,
    sameSite: 'lax',
    secure: process.env.NODE_ENV === 'production',
    path: '/help-desk',
    maxAge: HELPDESK_COOKIE_MAX_AGE,
  });

  redirect('/help-desk');
}
