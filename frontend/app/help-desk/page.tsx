import { cookies } from 'next/headers';
import Link from 'next/link';
import { BrandMark } from '@/components/app/brand-mark';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { type Escalation, REASON_LABELS, getEscalations } from '@/lib/escalations';
import { HELPDESK_COOKIE, helpDeskToken, tokenMatches } from './auth';
import { UnlockForm } from './unlock-form';

// Reads the agent's SQLite file at request time, so it must run on Node and must
// never be cached — an escalation that is minutes stale is a safety problem.
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export const metadata = {
  title: 'Aanya — Human help desk',
  description: 'Open requests where Aanya asked a human to take over.',
};

interface HelpDeskPageProps {
  searchParams: Promise<{ token?: string }>;
}

function urgencyVariant(urgency: string): 'destructive' | 'default' | 'secondary' {
  if (urgency === 'emergency') return 'destructive';
  if (urgency === 'soon') return 'default';
  return 'secondary';
}

function formatWhen(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso || 'unknown time';
  return parsed.toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' });
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="space-y-0.5">
      <p className="text-muted-foreground text-xs tracking-wide uppercase">{label}</p>
      <p className="text-sm">{value || '—'}</p>
    </div>
  );
}

function RequestCard({ escalation }: { escalation: Escalation }) {
  const reason = REASON_LABELS[escalation.reason_code] ?? escalation.reason_code;

  return (
    <Card className="gap-4">
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="font-mono text-base" style={{ color: '#6347EE' }}>
            {escalation.reference_id || `#${escalation.escalation_id}`}
          </CardTitle>
          <Badge variant={urgencyVariant(escalation.urgency)}>{escalation.urgency}</Badge>
          <Badge variant="outline">{reason}</Badge>
          {escalation.status !== 'open' ? (
            <Badge variant="secondary">{escalation.status}</Badge>
          ) : null}
          <span className="text-muted-foreground ml-auto text-xs">
            {formatWhen(escalation.created_at)}
          </span>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <Field
          label="Who needs help"
          value={`${escalation.caller_name} (${escalation.phone_number || 'no number shared'})`}
        />
        <Field label="What happened" value={escalation.what_happened} />
        <Field label="What Aanya already did" value={escalation.already_checked} />
        <Separator />
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
          <Field label="Language" value={escalation.language} />
          <Field label="Preferred follow-up" value={escalation.followup_method} />
          <Field
            label="Notification"
            value={
              escalation.delivery_status === 'saved_only'
                ? 'saved here only (no webhook configured)'
                : escalation.delivery_status
            }
          />
        </div>
      </CardContent>
    </Card>
  );
}

function HelpDeskNav() {
  return (
    <header className="aanya-nav sticky top-0 z-50 border-b border-slate-200/70 bg-[#f8f9fd]/85 backdrop-blur-xl">
      <div className="mx-auto flex h-[72px] w-full max-w-[1240px] items-center justify-between px-5 sm:px-8 lg:px-10">
        <Link
          href="/"
          className="group flex items-center gap-3 rounded-xl focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-600"
          aria-label="Aanya home"
        >
          <BrandMark className="transition-transform duration-300 group-hover:scale-105 group-hover:-rotate-3" />
          <span className="text-xl font-bold tracking-[-0.03em] text-[#111a2e]">Aanya</span>
        </Link>

        <nav aria-label="Dashboard navigation" className="flex items-center gap-3 sm:gap-6">
          <Link
            href="/analytics"
            className="hidden rounded-md text-sm font-medium text-slate-600 transition-colors hover:text-[#111a2e] focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-600 sm:block"
          >
            Analytics 📊
          </Link>
          <Link href="/" className="aanya-primary-button h-11 rounded-full px-5 text-sm font-semibold">
            Back to Aanya
          </Link>
        </nav>
      </div>
    </header>
  );
}

export default async function HelpDeskPage({ searchParams }: HelpDeskPageProps) {
  const expectedToken = helpDeskToken();
  const { token } = await searchParams;

  // These rows hold health complaints and phone numbers, so the page is gated
  // whenever HELPDESK_TOKEN is set. With no token set it stays open — fine on
  // localhost for a demo, and the banner below says so out loud.
  //
  // Two ways in, because the nav bar cannot carry the token: a ?token=… query
  // string for direct links, or the httpOnly cookie left behind by a previous
  // unlock. The cookie is what makes "Help Desk 🆘" in the nav a working link.
  const remembered = (await cookies()).get(HELPDESK_COOKIE)?.value;
  const authorised =
    !expectedToken ||
    tokenMatches(token, expectedToken) ||
    tokenMatches(remembered, expectedToken);

  if (!authorised) {
    return (
      <div className="aanya-page min-h-svh">
        <HelpDeskNav />
        <main className="mx-auto flex min-h-[calc(100svh-72px)] max-w-md items-center px-6">
          <Card className="w-full">
            <CardHeader>
              <CardTitle>Unlock the help desk</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4 text-sm">
              <p className="text-muted-foreground">
                These are real caller health details, so the page is token-gated. Enter it once and
                this browser stays unlocked for 12 hours.
              </p>
              <UnlockForm />
            </CardContent>
          </Card>
        </main>
      </div>
    );
  }

  const { rows, dbPath, error, tableMissing } = getEscalations();
  const open = rows.filter((row) => row.status === 'open');
  const closed = rows.filter((row) => row.status !== 'open');

  return (
    <div className="aanya-page min-h-svh">
      <HelpDeskNav />
      <main className="mx-auto max-w-3xl space-y-6 px-6 py-10">
        <header className="space-y-2">
          <h1 className="text-2xl font-semibold">Human help desk</h1>
          <p className="text-muted-foreground text-sm">
            Cases where Aanya stopped and asked a human to take over — a red-flag symptom, or a
            request for a diagnosis, a medicine or a report reading. Every caller here agreed to
            share these details.
          </p>
          {!expectedToken ? (
            <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
              No <span className="font-mono">HELPDESK_TOKEN</span> is set, so anyone who can reach
              this server can read these caller health details. Set one in{' '}
              <span className="font-mono">.env.local</span> before exposing this page beyond
              localhost.
            </p>
          ) : null}
        </header>
        {error ? (
          <Card>
            <CardHeader>
              <CardTitle>Could not read the escalation database</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <p className="text-muted-foreground">Tried to open:</p>
              <p className="font-mono text-xs break-all">{dbPath}</p>
              <p className="text-muted-foreground text-xs">{error}</p>
              <p className="text-muted-foreground text-xs">
                Set <span className="font-mono">ESCALATION_DB_PATH</span> in{' '}
                <span className="font-mono">.env.local</span> if the database lives elsewhere.
              </p>
            </CardContent>
          </Card>
        ) : null}

        {!error && open.length === 0 ? (
          <Card>
            <CardHeader>
              <CardTitle>No open requests</CardTitle>
            </CardHeader>
            <CardContent className="text-muted-foreground text-sm">
              {tableMissing
                ? 'The agent has not filed any escalations yet — run a call first.'
                : 'Nothing is waiting for a human right now.'}
            </CardContent>
          </Card>
        ) : null}

        {open.length > 0 ? (
          <section className="space-y-4">
            <h2 className="text-sm font-medium tracking-wide uppercase">Open · {open.length}</h2>
            {open.map((row) => (
              <RequestCard key={row.escalation_id} escalation={row} />
            ))}
          </section>
        ) : null}

        {closed.length > 0 ? (
          <section className="space-y-4">
            <h2 className="text-muted-foreground text-sm font-medium tracking-wide uppercase">
              Closed · {closed.length}
            </h2>
            {closed.map((row) => (
              <RequestCard key={row.escalation_id} escalation={row} />
            ))}
          </section>
        ) : null}
      </main>
    </div>
  );
}
