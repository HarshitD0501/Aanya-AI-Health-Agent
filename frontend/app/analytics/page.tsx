'use client';

import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import {
  CheckCircle2,
  Clock,
  Globe,
  PhoneCall,
  PhoneIncoming,
  PhoneOutgoing,
  RefreshCw,
  ShieldCheck,
  TrendingUp,
  UserCheck,
  XCircle,
  Zap,
} from 'lucide-react';
import { BrandMark } from '@/components/app/brand-mark';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

interface CallLog {
  call_id: number;
  session_id: string;
  call_type: string;
  caller_identifier: string;
  outcome: string;
  outcome_reason: string;
  duration_sec: number;
  created_at: string;
}

interface AnalyticsData {
  total_calls: number;
  successful_calls: number;
  failed_calls: number;
  success_rate: string;
  recent_calls: CallLog[];
  tableMissing?: boolean;
  error?: string;
}

const CALL_TYPE_BADGES: Record<
  string,
  { label: string; icon: React.ElementType; className: string }
> = {
  inbound_browser: {
    label: 'Web Browser',
    icon: Globe,
    className: 'border-sky-200 bg-sky-50 text-sky-700',
  },
  inbound_sip: {
    label: 'Inbound SIP',
    icon: PhoneIncoming,
    className: 'border-violet-200 bg-violet-50 text-violet-700',
  },
  outbound_reminder: {
    label: 'Outbound SIP',
    icon: PhoneOutgoing,
    className: 'border-indigo-200 bg-indigo-50 text-indigo-700',
  },
};

function CallTypeBadge({ type }: { type: string }) {
  const config = CALL_TYPE_BADGES[type];
  if (!config) {
    return (
      <Badge variant="outline" className="border-slate-200 bg-slate-50 text-slate-600">
        {type}
      </Badge>
    );
  }

  const Icon = config.icon;
  return (
    <Badge variant="outline" className={`gap-1.5 font-medium ${config.className}`}>
      <Icon className="size-3" /> {config.label}
    </Badge>
  );
}

interface StatCardProps {
  label: string;
  value: string;
  valueClassName?: string;
  watermark: React.ElementType;
  watermarkClassName: string;
  children: React.ReactNode;
}

function StatCard({
  label,
  value,
  valueClassName,
  watermark: Watermark,
  watermarkClassName,
  children,
}: StatCardProps) {
  return (
    <Card className="aanya-feature-card relative gap-3 overflow-hidden border-slate-200 bg-white">
      <div
        className={`pointer-events-none absolute top-0 right-0 p-4 opacity-10 ${watermarkClassName}`}
      >
        <Watermark className="size-16" />
      </div>
      <CardHeader className="pb-0">
        <CardDescription className="text-xs font-bold tracking-[0.12em] text-slate-500 uppercase">
          {label}
        </CardDescription>
        <CardTitle
          className={`mt-1 text-3xl font-semibold tracking-[-0.04em] md:text-4xl ${valueClassName ?? 'text-[#10182b]'}`}
        >
          {value}
        </CardTitle>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

export default function AnalyticsPage() {
  const [data, setData] = useState<AnalyticsData | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [autoRefresh, setAutoRefresh] = useState<boolean>(true);
  const [lastUpdated, setLastUpdated] = useState<string>('');

  const fetchAnalytics = useCallback(async (showIndicator = false) => {
    if (showIndicator) setIsRefreshing(true);
    try {
      const res = await fetch('/api/analytics', { cache: 'no-store' });
      if (res.ok) {
        const json = await res.json();
        setData(json);
        setLastUpdated(new Date().toLocaleTimeString('en-IN', { hour12: true }));
      }
    } catch (err) {
      console.error('Failed to fetch analytics:', err);
    } finally {
      setLoading(false);
      setIsRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchAnalytics();
  }, [fetchAnalytics]);

  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(() => {
      fetchAnalytics(false);
    }, 3000);
    return () => clearInterval(interval);
  }, [autoRefresh, fetchAnalytics]);

  const formatTimestamp = (iso: string) => {
    try {
      const date = new Date(iso);
      if (isNaN(date.getTime())) return iso;
      return date.toLocaleString('en-IN', {
        dateStyle: 'medium',
        timeStyle: 'short',
      });
    } catch {
      return iso;
    }
  };

  return (
    <div className="aanya-page min-h-svh overflow-x-clip">
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
              href="/help-desk"
              className="hidden rounded-md text-sm font-medium text-slate-600 transition-colors hover:text-[#111a2e] focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-600 sm:block"
            >
              Help Desk 🆘
            </Link>
            <Link
              href="/"
              className="aanya-primary-button h-11 rounded-full px-5 text-sm font-semibold"
            >
              Back to Aanya
            </Link>
          </nav>
        </div>
      </header>

      <main className="relative isolate">
        <div
          className="aanya-dot-field pointer-events-none absolute inset-x-0 top-0 -z-30 h-[520px]"
          aria-hidden="true"
        />
        <div
          className="aanya-hero-glow aanya-hero-glow-one pointer-events-none absolute -z-20"
          aria-hidden="true"
        />

        <div className="mx-auto w-full max-w-[1240px] space-y-8 px-5 py-12 sm:px-8 sm:py-14 lg:px-10">
          {/* Page heading */}
          <div className="flex flex-col gap-6 border-b border-slate-200 pb-8 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <p className="aanya-kicker">Call analytics</p>
              <h1 className="mt-5 max-w-2xl text-4xl leading-[1.06] font-semibold tracking-[-0.04em] text-[#10182b] sm:text-[2.75rem]">
                Performance overview
              </h1>
              <p className="mt-4 max-w-xl text-base leading-7 text-slate-600">
                Voice-agent outcomes across browser and SIP sessions, updated live.
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <span className="inline-flex items-center gap-2 rounded-full border border-violet-200/80 bg-white/80 px-3.5 py-2 text-xs font-bold tracking-[0.08em] text-violet-700 uppercase shadow-sm backdrop-blur">
                <Zap className="size-3.5 fill-violet-500 text-violet-500" />
                Powered by Murf Falcon
              </span>

              <Button
                variant="outline"
                size="sm"
                onClick={() => fetchAnalytics(true)}
                disabled={isRefreshing}
                className="h-11 rounded-full border-slate-300 bg-white/80 px-5 font-semibold text-[#111a2e] shadow-sm backdrop-blur hover:border-slate-400 hover:bg-white"
              >
                <RefreshCw className={`mr-1 size-4 ${isRefreshing ? 'animate-spin' : ''}`} />
                Refresh
              </Button>

              <Button
                size="sm"
                onClick={() => setAutoRefresh(!autoRefresh)}
                aria-pressed={autoRefresh}
                className={
                  autoRefresh
                    ? 'aanya-primary-button h-11 rounded-full px-5 font-semibold'
                    : 'h-11 rounded-full border border-slate-300 bg-white/80 px-5 font-semibold text-[#111a2e] shadow-sm backdrop-blur hover:bg-white'
                }
              >
                <span className="relative mr-1 flex size-2">
                  {autoRefresh && (
                    <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-400 opacity-70 motion-reduce:animate-none" />
                  )}
                  <span
                    className={`relative inline-flex size-2 rounded-full ${autoRefresh ? 'bg-emerald-400' : 'bg-slate-400'}`}
                  />
                </span>
                {autoRefresh ? 'Live (3s)' : 'Paused'}
              </Button>
            </div>
          </div>

          {/* Privacy banner */}
          <div className="flex items-start gap-3 rounded-[22px] border border-slate-200 bg-white/80 p-4 text-xs leading-6 text-slate-600 shadow-sm backdrop-blur md:text-sm">
            <ShieldCheck className="mt-0.5 size-5 shrink-0 text-emerald-600" />
            <span>
              <strong className="font-semibold text-[#111a2e]">Caller privacy guaranteed:</strong>{' '}
              Phone numbers are masked (<span className="font-mono">+91 945****137</span>). No PINs,
              passwords, OTPs, medical records, or transcripts are stored or exposed on this
              dashboard.
            </span>
          </div>

          {/* KPI cards */}
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label="Total calls"
              value={loading ? '—' : String(data?.total_calls ?? 0)}
              watermark={PhoneCall}
              watermarkClassName="text-violet-500"
            >
              <p className="flex items-center gap-1.5 text-xs text-slate-500">
                <Clock className="size-3.5" />
                Browser &amp; SIP voice sessions
              </p>
            </StatCard>

            <StatCard
              label="Successful calls"
              value={loading ? '—' : String(data?.successful_calls ?? 0)}
              valueClassName="text-emerald-600"
              watermark={CheckCircle2}
              watermarkClassName="text-emerald-500"
            >
              <p className="flex items-center gap-1.5 text-xs font-medium text-emerald-700">
                <UserCheck className="size-3.5" />
                Guidance &amp; resolution provided
              </p>
            </StatCard>

            <StatCard
              label="Failed calls"
              value={loading ? '—' : String(data?.failed_calls ?? 0)}
              valueClassName="text-rose-600"
              watermark={XCircle}
              watermarkClassName="text-rose-500"
            >
              <p className="flex items-center gap-1.5 text-xs font-medium text-rose-600">
                <XCircle className="size-3.5" />
                Early drop-off / short call (&lt;5s)
              </p>
            </StatCard>

            <StatCard
              label="Success rate"
              value={loading ? '—' : (data?.success_rate ?? '0%')}
              valueClassName="text-[#6042df]"
              watermark={TrendingUp}
              watermarkClassName="text-violet-500"
            >
              <div className="mt-1 h-2 w-full rounded-full bg-slate-100">
                <div
                  className="h-2 rounded-full bg-gradient-to-r from-[#3158f4] via-[#7049f5] to-[#d93491] transition-all duration-500"
                  style={{
                    width: data?.total_calls
                      ? `${(data.successful_calls / data.total_calls) * 100}%`
                      : '0%',
                  }}
                />
              </div>
            </StatCard>
          </div>

          {/* Real-time call log */}
          <Card className="gap-0 overflow-hidden border-slate-200 bg-white shadow-[0_24px_64px_-46px_rgb(31_37_68_/_48%)]">
            <CardHeader className="flex flex-row items-center justify-between border-b border-slate-200 pb-5">
              <div>
                <CardTitle className="flex items-center gap-2 text-lg font-semibold tracking-[-0.025em] text-[#111a2e]">
                  <Clock className="size-5 text-violet-600" />
                  Real-time call outcomes
                </CardTitle>
                <CardDescription className="mt-1 text-xs text-slate-500">
                  Live outcomes recorded from browser and SIP voice interactions
                </CardDescription>
              </div>
              {lastUpdated && (
                <span className="font-mono text-xs text-slate-400">Updated at {lastUpdated}</span>
              )}
            </CardHeader>

            <CardContent className="overflow-x-auto p-0">
              {loading ? (
                <div className="p-12 text-center text-sm text-slate-500">
                  Loading call analytics…
                </div>
              ) : !data?.recent_calls || data.recent_calls.length === 0 ? (
                <div className="space-y-2 p-12 text-center">
                  <PhoneCall className="mx-auto size-10 text-slate-300" />
                  <p className="text-sm text-slate-600">No calls recorded yet.</p>
                  <p className="text-xs text-slate-500">
                    Start a call from the home page or via SIP to see real-time updates here.
                  </p>
                </div>
              ) : (
                <table className="w-full text-left text-sm text-slate-700">
                  <thead className="border-b border-slate-200 bg-[#f3f4fa] text-xs tracking-[0.08em] text-slate-500 uppercase">
                    <tr>
                      <th className="px-4 py-3.5 font-semibold">ID</th>
                      <th className="px-4 py-3.5 font-semibold">Channel</th>
                      <th className="px-4 py-3.5 font-semibold">Caller (masked)</th>
                      <th className="px-4 py-3.5 font-semibold">Outcome</th>
                      <th className="px-4 py-3.5 font-semibold">Outcome detail</th>
                      <th className="px-4 py-3.5 font-semibold">Duration</th>
                      <th className="px-4 py-3.5 font-semibold">Timestamp</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-200">
                    {data.recent_calls.map((call) => {
                      const isSuccess = call.outcome === 'success';
                      return (
                        <tr key={call.call_id} className="transition-colors hover:bg-[#f8f8fd]">
                          <td className="px-4 py-3.5 font-mono text-xs text-slate-400">
                            #{call.call_id}
                          </td>
                          <td className="px-4 py-3.5">
                            <CallTypeBadge type={call.call_type} />
                          </td>
                          <td className="px-4 py-3.5 font-mono text-xs text-[#111a2e]">
                            {call.caller_identifier || 'Anonymous'}
                          </td>
                          <td className="px-4 py-3.5">
                            {isSuccess ? (
                              <Badge
                                variant="outline"
                                className="gap-1.5 border-emerald-200 bg-emerald-50 font-medium text-emerald-700"
                              >
                                <CheckCircle2 className="size-3" /> Successful
                              </Badge>
                            ) : (
                              <Badge
                                variant="outline"
                                className="gap-1.5 border-rose-200 bg-rose-50 font-medium text-rose-700"
                              >
                                <XCircle className="size-3" /> Failed
                              </Badge>
                            )}
                          </td>
                          <td className="px-4 py-3.5 text-xs text-slate-600">
                            {call.outcome_reason || '—'}
                          </td>
                          <td className="px-4 py-3.5 font-mono text-xs text-slate-600">
                            {call.duration_sec ? `${call.duration_sec.toFixed(1)}s` : '0.0s'}
                          </td>
                          <td className="px-4 py-3.5 text-xs whitespace-nowrap text-slate-500">
                            {formatTimestamp(call.created_at)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </CardContent>
          </Card>
        </div>
      </main>

      <footer className="px-5 py-9 sm:px-8 lg:px-10">
        <div className="mx-auto flex max-w-[1240px] flex-col gap-4 border-t border-slate-200 pt-8 text-sm text-slate-500 md:flex-row md:items-center md:justify-between">
          <p className="max-w-xl leading-6">
            Aanya provides general health information, not medical diagnosis or emergency care.
          </p>
          <p>Murf Falcon + LiveKit · © 2026 Aanya</p>
        </div>
      </footer>
    </div>
  );
}
