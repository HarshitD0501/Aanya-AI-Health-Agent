'use client';

import { useActionState } from 'react';
import { Button } from '@/components/ui/button';
import { type UnlockState, unlockHelpDesk } from './actions';

/** The one-time unlock. After this the cookie is set and the nav link goes straight in. */
export function UnlockForm() {
  const [state, formAction, pending] = useActionState<UnlockState, FormData>(unlockHelpDesk, {});

  return (
    <form action={formAction} className="space-y-3">
      <label htmlFor="helpdesk-token" className="text-muted-foreground block text-sm">
        Access token
      </label>
      <input
        id="helpdesk-token"
        name="token"
        type="password"
        autoComplete="off"
        autoFocus
        required
        placeholder="HELPDESK_TOKEN"
        aria-describedby={state.error ? 'helpdesk-token-error' : undefined}
        className="h-11 w-full rounded-xl border border-slate-300 bg-white px-4 font-mono text-sm text-[#111a2e] shadow-sm outline-none focus-visible:border-violet-500 focus-visible:ring-2 focus-visible:ring-violet-500/30"
      />
      {state.error ? (
        <p id="helpdesk-token-error" role="alert" className="text-sm text-rose-600">
          {state.error}
        </p>
      ) : null}
      <Button type="submit" disabled={pending} className="h-11 w-full rounded-xl font-semibold">
        {pending ? 'Checking…' : 'Open help desk'}
      </Button>
    </form>
  );
}
