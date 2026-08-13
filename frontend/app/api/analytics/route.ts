import { NextResponse } from 'next/server';
import { getCallAnalytics } from '@/lib/analytics';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET() {
  // `dbPath` is dropped before the response leaves the server: this route is
  // unauthenticated, and the resolved path exposes the OS username and the
  // machine's directory layout. The dashboard never reads it — only the
  // server-side error logging below does.
  const { dbPath, ...analytics } = getCallAnalytics(50);

  if (analytics.error) {
    console.error(`[analytics] read failed for ${dbPath}: ${analytics.error}`);
  }

  return NextResponse.json(analytics);
}
