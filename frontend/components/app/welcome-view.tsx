'use client';

import type { SVGProps } from 'react';
import { useCallback, useState } from 'react';
import Link from 'next/link';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import Magnet from '@/components/Magnet';
import SpotlightCard from '@/components/SpotlightCard';
import { BrandMark } from '@/components/app/brand-mark';
import { cn } from '@/lib/shadcn/utils';

// `route: true` means a real page, so it gets next/link: Next prefetches it and
// swaps it in client-side. A plain <a> made "Analytics" and "Help Desk" a full
// document load, which in dev also waits on that route compiling - the pause
// that felt like the button was doing nothing. Hash targets stay plain anchors;
// Link on a same-page hash just adds overhead.
const NAV_ITEMS = [
  { label: 'Home', href: '#home', route: false },
  { label: 'Analytics 📊', href: '/analytics', route: true },
  { label: 'Help Desk 🆘', href: '/help-desk', route: true },
  { label: 'About', href: '#about', route: false },
  { label: 'FAQ', href: '#faq', route: false },
] as const;

const HERO_SUPPORT_WORDS = ['Here', 'to', 'listen,', 'guide,', 'and', 'support.'] as const;

const FEATURES = [
  {
    icon: 'conversation',
    title: 'Talk naturally',
    description:
      'Ask a health question in your own words. No forms, menus, or clinical language required.',
  },
  {
    icon: 'clarity',
    title: 'Find a clearer next step',
    description: 'Aanya turns a spoken question into an approachable conversation you can follow.',
  },
  {
    icon: 'voice',
    title: 'Built for responsive voice',
    description:
      'Murf Falcon and LiveKit keep the experience fast, fluid, and ready for real conversation.',
  },
] as const;

const STEPS = [
  {
    number: '01',
    title: 'Start the conversation',
    description: 'Tap Get started and allow microphone access when your browser asks.',
  },
  {
    number: '02',
    title: 'Ask in your own words',
    description:
      'Speak naturally, just as you would when explaining a concern to someone you trust.',
  },
  {
    number: '03',
    title: 'Hear a clear response',
    description: 'Aanya replies by voice so the conversation stays simple and easy to follow.',
  },
] as const;

const FAQS = [
  {
    question: 'What can I ask Aanya?',
    answer:
      'You can ask general health and wellness questions or use Aanya as a calm starting point for understanding what to do next. Aanya does not diagnose conditions or replace a qualified clinician.',
  },
  {
    question: 'Is Aanya an emergency service?',
    answer:
      'No. If you or someone else may be in immediate danger, contact your local emergency services right away. Do not wait for a voice-agent response.',
  },
  {
    question: 'How does the voice conversation begin?',
    answer:
      'Select any Get started button, then approve microphone access. The existing LiveKit session will connect and Aanya will be ready to listen.',
  },
  {
    question: 'What technology powers Aanya?',
    answer:
      'Aanya uses Murf Falcon for expressive, low-latency voice output and LiveKit for the real-time conversation experience.',
  },
] as const;

function ArrowIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 20 20" fill="none" aria-hidden="true" {...props}>
      <path
        d="M4 10h11m-4.5-4.5L15 10l-4.5 4.5"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function MenuIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" {...props}>
      <path d="M5 8h14M5 16h14" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function CloseIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" {...props}>
      <path
        d="m7 7 10 10M17 7 7 17"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}

function MicrophoneIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 32 32" fill="none" aria-hidden="true" {...props}>
      <rect x="11" y="5" width="10" height="16" rx="5" stroke="currentColor" strokeWidth="2" />
      <path
        d="M7.5 16.5v.5a8.5 8.5 0 0 0 17 0v-.5M16 25.5V29m-4 0h8"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

function FeatureIcon({ name }: { name: (typeof FEATURES)[number]['icon'] }) {
  if (name === 'conversation') {
    return (
      <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" className="size-6">
        <path
          d="M5 17.5 3.5 21l4.2-1.8c1.25.52 2.72.8 4.3.8 5.25 0 9-3.25 9-8s-3.75-8-9-8-9 3.25-9 8c0 2.15.77 4.05 2 5.5Z"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path d="M8 12h8M8 9h5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    );
  }

  if (name === 'clarity') {
    return (
      <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" className="size-6">
        <path
          d="M12 3a7 7 0 0 0-4.35 12.48c.86.69 1.35 1.3 1.35 2.02h6c0-.72.49-1.33 1.35-2.02A7 7 0 0 0 12 3Z"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
        <path
          d="M9.5 21h5M9 17.5h6"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
      </svg>
    );
  }

  return (
    <svg viewBox="0 0 24 24" fill="none" aria-hidden="true" className="size-6">
      <path
        d="M4 12h2l1.5-5 3 10 3-13 3 12 1.5-4H20"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function Reveal({
  children,
  className,
  delay = 0,
}: {
  children: React.ReactNode;
  className?: string;
  delay?: number;
}) {
  const shouldReduceMotion = useReducedMotion();

  return (
    <motion.div
      initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: 28 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.18 }}
      transition={{ duration: shouldReduceMotion ? 0 : 0.65, delay, ease: [0.22, 1, 0.36, 1] }}
      className={className}
    >
      {children}
    </motion.div>
  );
}

interface WelcomeViewProps {
  startButtonText: string;
  onStartCall: () => Promise<void>;
}

export const WelcomeView = ({
  startButtonText,
  onStartCall,
  ref,
  className,
  ...props
}: React.ComponentProps<'div'> & WelcomeViewProps) => {
  const shouldReduceMotion = useReducedMotion();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  const handleStart = useCallback(async () => {
    if (isStarting) return;

    setMobileMenuOpen(false);
    setStartError(null);
    setIsStarting(true);

    try {
      await onStartCall();
    } catch {
      setIsStarting(false);
      setStartError(
        'Aanya could not start the voice session. Please check your connection and try again.'
      );
    }
  }, [isStarting, onStartCall]);

  const startLabel = isStarting ? 'Connecting…' : startButtonText;

  return (
    <div ref={ref} className={cn('aanya-page min-h-svh overflow-x-clip', className)} {...props}>
      <AnimatePresence>
        {startError ? (
          <motion.div
            role="alert"
            initial={{ opacity: 0, y: -16 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -16 }}
            className="fixed top-20 left-1/2 z-[60] flex w-[min(92vw,580px)] -translate-x-1/2 items-start gap-3 rounded-2xl border border-red-200 bg-white p-4 text-sm text-red-950 shadow-xl shadow-slate-950/10"
          >
            <span className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-full bg-red-50 font-bold text-red-600">
              !
            </span>
            <p className="flex-1 leading-6">{startError}</p>
            <button
              type="button"
              onClick={() => setStartError(null)}
              className="rounded-md p-1 text-red-700 transition-colors hover:bg-red-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-500"
            >
              <span className="sr-only">Dismiss error</span>
              <CloseIcon className="size-5" />
            </button>
          </motion.div>
        ) : null}
      </AnimatePresence>

      <header className="aanya-nav sticky top-0 z-50 border-b border-slate-200/70 bg-[#f8f9fd]/85 backdrop-blur-xl">
        <div className="mx-auto flex h-[72px] w-full max-w-[1240px] items-center justify-between px-5 sm:px-8 lg:px-10">
          <a
            href="#home"
            className="group flex items-center gap-3 rounded-xl focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-600"
            aria-label="Aanya home"
          >
            <BrandMark className="transition-transform duration-300 group-hover:scale-105 group-hover:-rotate-3" />
            <span className="text-xl font-bold tracking-[-0.03em] text-[#111a2e]">Aanya</span>
          </a>

          <nav aria-label="Primary navigation" className="hidden items-center gap-9 md:flex">
            {NAV_ITEMS.map((item) => {
              const className =
                'rounded-md text-sm font-medium text-slate-600 transition-colors hover:text-[#111a2e] focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-600';
              return item.route ? (
                <Link key={item.href} href={item.href} prefetch className={className}>
                  {item.label}
                </Link>
              ) : (
                <a key={item.href} href={item.href} className={className}>
                  {item.label}
                </a>
              );
            })}
          </nav>

          <div className="hidden md:block">
            <button
              type="button"
              data-start-cta="navigation"
              onClick={handleStart}
              disabled={isStarting}
              className="aanya-primary-button group h-11 rounded-full px-6 text-sm font-semibold"
            >
              {startLabel}
              <ArrowIcon className="size-4 transition-transform duration-300 group-hover:translate-x-0.5" />
            </button>
          </div>

          <button
            type="button"
            aria-expanded={mobileMenuOpen}
            aria-controls="aanya-mobile-navigation"
            onClick={() => setMobileMenuOpen((open) => !open)}
            className="grid size-11 place-items-center rounded-full border border-slate-200 bg-white text-[#111a2e] shadow-sm transition-colors hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-600 md:hidden"
          >
            <span className="sr-only">{mobileMenuOpen ? 'Close menu' : 'Open menu'}</span>
            {mobileMenuOpen ? <CloseIcon className="size-5" /> : <MenuIcon className="size-5" />}
          </button>
        </div>

        <AnimatePresence initial={false}>
          {mobileMenuOpen ? (
            <motion.nav
              id="aanya-mobile-navigation"
              aria-label="Mobile navigation"
              initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: -12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={shouldReduceMotion ? { opacity: 0 } : { opacity: 0, y: -12 }}
              transition={{ duration: shouldReduceMotion ? 0 : 0.22 }}
              className="absolute inset-x-4 top-[64px] rounded-3xl border border-slate-200 bg-white p-3 shadow-2xl shadow-slate-950/10 md:hidden"
            >
              {NAV_ITEMS.map((item) => (
                <a
                  key={item.href}
                  href={item.href}
                  onClick={() => setMobileMenuOpen(false)}
                  className="block rounded-2xl px-4 py-3.5 font-medium text-[#111a2e] transition-colors hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-violet-600"
                >
                  {item.label}
                </a>
              ))}
              <button
                type="button"
                data-start-cta="mobile-navigation"
                onClick={handleStart}
                disabled={isStarting}
                className="aanya-primary-button mt-2 h-12 w-full rounded-2xl px-5 font-semibold"
              >
                {startLabel}
                <ArrowIcon className="size-4" />
              </button>
            </motion.nav>
          ) : null}
        </AnimatePresence>
      </header>

      <main>
        <section
          id="home"
          className="aanya-section relative isolate overflow-hidden border-b border-slate-200/70"
        >
          <div
            className="aanya-dot-field pointer-events-none absolute inset-0 -z-30"
            aria-hidden="true"
          />
          <div
            className="aanya-hero-glow aanya-hero-glow-one pointer-events-none absolute -z-20"
            aria-hidden="true"
          />
          <div
            className="aanya-hero-glow aanya-hero-glow-two pointer-events-none absolute -z-20"
            aria-hidden="true"
          />

          <div className="mx-auto grid min-h-[calc(100svh-72px)] w-full max-w-[1240px] items-start gap-12 px-5 pt-8 pb-16 sm:px-8 sm:pt-10 sm:pb-20 lg:grid-cols-[1.04fr_.96fr] lg:gap-16 lg:px-10 lg:pt-10 lg:pb-24">
            <div className="relative z-10 max-w-2xl">
              <motion.div
                initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: shouldReduceMotion ? 0 : 0.5, delay: 0.05 }}
                className="mb-7 inline-flex items-center gap-2 rounded-full border border-violet-200/80 bg-white/80 px-3.5 py-2 text-xs font-bold tracking-[0.08em] text-violet-700 uppercase shadow-sm backdrop-blur"
              >
                <span className="relative flex size-2">
                  <span className="absolute inline-flex size-full animate-ping rounded-full bg-violet-500 opacity-50 motion-reduce:animate-none" />
                  <span className="relative inline-flex size-2 rounded-full bg-violet-600" />
                </span>
                Health Advisory Voice Agent
              </motion.div>

              <h1 className="aanya-hero-title max-w-[760px] text-[#10182b]">
                <span className="aanya-hero-title-line flex items-baseline whitespace-nowrap">
                  <motion.span
                    initial={
                      shouldReduceMotion
                        ? { filter: 'blur(0px)', opacity: 1, y: 0 }
                        : { filter: 'blur(10px)', opacity: 0, y: 34 }
                    }
                    animate={{ filter: 'blur(0px)', opacity: 1, y: 0 }}
                    transition={{
                      duration: shouldReduceMotion ? 0 : 0.55,
                      delay: shouldReduceMotion ? 0 : 0.08,
                      ease: [0.22, 1, 0.36, 1],
                    }}
                    className="inline-block"
                  >
                    Meet&nbsp;
                  </motion.span>
                  <motion.span
                    initial={
                      shouldReduceMotion
                        ? { filter: 'blur(0px)', opacity: 1, y: 0 }
                        : { filter: 'blur(10px)', opacity: 0, y: 34 }
                    }
                    animate={{ filter: 'blur(0px)', opacity: 1, y: 0 }}
                    transition={{
                      duration: shouldReduceMotion ? 0 : 0.62,
                      delay: shouldReduceMotion ? 0 : 0.16,
                      ease: [0.22, 1, 0.36, 1],
                    }}
                    className="aanya-hero-name inline-block"
                  >
                    Aanya.
                  </motion.span>
                </span>
                <span
                  className="aanya-hero-support block"
                  aria-label="Here to listen, guide, and support."
                >
                  {HERO_SUPPORT_WORDS.map((word, index) => (
                    <motion.span
                      key={word}
                      initial={
                        shouldReduceMotion
                          ? { filter: 'blur(0px)', opacity: 1, y: 0 }
                          : { filter: 'blur(10px)', opacity: 0, y: 30 }
                      }
                      animate={{ filter: 'blur(0px)', opacity: 1, y: 0 }}
                      transition={{
                        duration: shouldReduceMotion ? 0 : 0.5,
                        delay: shouldReduceMotion ? 0 : 0.28 + index * 0.065,
                        ease: [0.22, 1, 0.36, 1],
                      }}
                      className="inline-block"
                      aria-hidden="true"
                    >
                      {word}
                      {index < HERO_SUPPORT_WORDS.length - 1 ? '\u00a0' : null}
                    </motion.span>
                  ))}
                </span>
              </h1>

              <motion.p
                initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: shouldReduceMotion ? 0 : 0.58, delay: 0.42 }}
                className="mt-7 max-w-xl text-lg leading-8 text-slate-600 sm:text-xl"
              >
                Speak naturally and get clear, conversational health information from a voice agent
                designed to make the first step feel simpler.
              </motion.p>

              <motion.div
                initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: shouldReduceMotion ? 0 : 0.58, delay: 0.52 }}
                className="mt-8 flex flex-col gap-3 sm:flex-row sm:items-center"
              >
                <Magnet
                  padding={44}
                  magnetStrength={7}
                  disabled={Boolean(shouldReduceMotion) || isStarting}
                  wrapperClassName="w-fit"
                >
                  <button
                    type="button"
                    data-start-cta="hero"
                    onClick={handleStart}
                    disabled={isStarting}
                    className="aanya-primary-button group h-14 rounded-full px-7 text-base font-semibold shadow-lg shadow-violet-900/15"
                  >
                    {startLabel}
                    <ArrowIcon className="size-5 transition-transform duration-300 group-hover:translate-x-1" />
                  </button>
                </Magnet>
                <a
                  href="#about"
                  className="inline-flex h-14 items-center justify-center rounded-full border border-slate-300 bg-white/80 px-7 text-base font-semibold text-[#111a2e] shadow-sm backdrop-blur transition-all hover:-translate-y-0.5 hover:border-slate-400 hover:bg-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-violet-600 motion-reduce:transform-none"
                >
                  See how it works
                </a>
              </motion.div>

              <motion.div
                initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ duration: shouldReduceMotion ? 0 : 0.5, delay: 0.68 }}
                className="mt-8 flex flex-wrap items-center gap-x-5 gap-y-3 text-sm text-slate-500"
              >
                <span className="inline-flex items-center gap-2">
                  <span className="size-1.5 rounded-full bg-emerald-500" />
                  Voice-first experience
                </span>
                <span className="inline-flex items-center gap-2">
                  <span className="size-1.5 rounded-full bg-violet-500" />
                  Murf Falcon + LiveKit
                </span>
              </motion.div>
            </div>

            <motion.div
              initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, scale: 0.94, y: 24 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              transition={{
                duration: shouldReduceMotion ? 0 : 0.85,
                delay: 0.22,
                ease: [0.22, 1, 0.36, 1],
              }}
              className="relative mx-auto w-full max-w-[560px] lg:mx-0"
            >
              <div className="aanya-voice-stage relative aspect-[1/1.05] overflow-hidden rounded-[40px] border border-white/90 bg-white/65 shadow-[0_40px_120px_-42px_rgba(65,51,140,0.4)] backdrop-blur-xl">
                <img
                  src="/aanya-advisor.jpg"
                  alt="Aanya — AI Health Advisor"
                  className="absolute inset-0 h-full w-full rounded-[40px] object-cover"
                />

                <div className="absolute right-5 bottom-5 left-5 flex items-center gap-3 rounded-2xl border border-white bg-white/90 p-3.5 shadow-lg shadow-slate-950/5 backdrop-blur sm:right-7 sm:bottom-7 sm:left-7 sm:p-4">
                  <BrandMark className="size-9 rounded-xl" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-bold text-[#111a2e]">Aanya is ready</p>
                    <p className="truncate text-xs text-slate-500">
                      Tap Get started and begin speaking
                    </p>
                  </div>
                  <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-[10px] font-bold tracking-wider text-emerald-700 uppercase">
                    Live
                  </span>
                </div>
              </div>
            </motion.div>
          </div>
        </section>

        <section id="about" className="aanya-section bg-white py-24 sm:py-28 lg:py-32">
          <div className="mx-auto w-full max-w-[1240px] px-5 sm:px-8 lg:px-10">
            <Reveal className="grid gap-8 lg:grid-cols-[.78fr_1.22fr] lg:items-end">
              <div>
                <p className="aanya-kicker">About Aanya</p>
                <h2 className="mt-5 max-w-lg text-4xl leading-[1.03] font-semibold tracking-[-0.045em] text-[#10182b] sm:text-5xl">
                  A calmer first step for everyday health questions.
                </h2>
              </div>
              <p className="max-w-2xl text-lg leading-8 text-slate-600 lg:justify-self-end">
                Aanya makes general health information feel easier to access by turning it into a
                natural voice conversation. Speak, listen, and leave with a clearer understanding of
                what to consider next.
              </p>
            </Reveal>

            <div className="mt-14 grid gap-5 md:grid-cols-3">
              {FEATURES.map((feature, index) => (
                <Reveal key={feature.title} delay={index * 0.08}>
                  <SpotlightCard
                    spotlightColor="rgba(99, 71, 238, 0.14)"
                    className="aanya-feature-card h-full min-h-[300px] border-slate-200 bg-[#f8f8fd] p-7 sm:p-8"
                  >
                    <div className="relative z-10 flex h-full flex-col">
                      <span className="grid size-12 place-items-center rounded-2xl border border-violet-100 bg-white text-violet-700 shadow-sm">
                        <FeatureIcon name={feature.icon} />
                      </span>
                      <div className="mt-auto pt-16">
                        <h3 className="text-xl font-semibold tracking-[-0.025em] text-[#111a2e]">
                          {feature.title}
                        </h3>
                        <p className="mt-3 leading-7 text-slate-600">{feature.description}</p>
                      </div>
                    </div>
                  </SpotlightCard>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        <section className="aanya-section border-y border-slate-200 bg-[#f3f4fa] py-24 sm:py-28">
          <div className="mx-auto w-full max-w-[1240px] px-5 sm:px-8 lg:px-10">
            <Reveal className="text-center">
              <p className="aanya-kicker">How it works</p>
              <h2 className="mx-auto mt-5 max-w-2xl text-4xl leading-tight font-semibold tracking-[-0.045em] text-[#10182b] sm:text-5xl">
                From question to conversation in one tap.
              </h2>
            </Reveal>

            <div className="relative mt-16 grid gap-5 lg:grid-cols-3">
              <div
                className="absolute top-[27px] right-[16%] left-[16%] hidden border-t border-dashed border-violet-300 lg:block"
                aria-hidden="true"
              />
              {STEPS.map((step, index) => (
                <Reveal key={step.number} delay={index * 0.08} className="relative">
                  <article className="h-full rounded-[28px] border border-slate-200 bg-white p-7 shadow-sm sm:p-8">
                    <span className="relative z-10 inline-grid size-14 place-items-center rounded-full bg-[#111a2e] font-mono text-sm font-bold text-white shadow-lg shadow-slate-950/15">
                      {step.number}
                    </span>
                    <h3 className="mt-8 text-xl font-semibold tracking-[-0.025em] text-[#111a2e]">
                      {step.title}
                    </h3>
                    <p className="mt-3 leading-7 text-slate-600">{step.description}</p>
                  </article>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        <section id="faq" className="aanya-section bg-white py-24 sm:py-28 lg:py-32">
          <div className="mx-auto grid w-full max-w-[1120px] gap-14 px-5 sm:px-8 lg:grid-cols-[.72fr_1.28fr] lg:px-10">
            <Reveal>
              <p className="aanya-kicker">Questions, answered</p>
              <h2 className="mt-5 text-4xl leading-[1.05] font-semibold tracking-[-0.045em] text-[#10182b] sm:text-5xl">
                A little clarity before you begin.
              </h2>
              <p className="mt-5 max-w-md text-lg leading-8 text-slate-600">
                Aanya is designed for general health guidance, with clear boundaries around urgent
                or clinical care.
              </p>
            </Reveal>

            <Reveal className="divide-y divide-slate-200 border-y border-slate-200">
              {FAQS.map((faq) => (
                <details key={faq.question} className="group py-1">
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-6 rounded-lg py-6 text-left text-lg font-semibold tracking-[-0.02em] text-[#111a2e] transition-colors outline-none hover:text-violet-700 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-600 sm:text-xl">
                    {faq.question}
                    <span className="relative grid size-8 shrink-0 place-items-center rounded-full border border-slate-200 bg-slate-50 text-slate-600 transition-transform duration-300 group-open:rotate-45">
                      <span className="absolute h-px w-3 bg-current" />
                      <span className="absolute h-3 w-px bg-current" />
                    </span>
                  </summary>
                  <p className="max-w-2xl pr-12 pb-6 leading-7 text-slate-600">{faq.answer}</p>
                </details>
              ))}
            </Reveal>
          </div>
        </section>

        <section className="px-5 pb-5 sm:px-8 sm:pb-8 lg:px-10">
          <Reveal className="aanya-cta-panel relative mx-auto max-w-[1240px] overflow-hidden rounded-[38px] bg-[#111a2e] px-6 py-16 text-center text-white sm:px-10 sm:py-20 lg:py-24">
            <div
              className="aanya-cta-glow pointer-events-none absolute inset-0"
              aria-hidden="true"
            />
            <div className="relative z-10 mx-auto max-w-3xl">
              <p className="text-xs font-bold tracking-[0.16em] text-violet-200 uppercase">
                Aanya is ready when you are
              </p>
              <h2 className="mt-5 text-4xl leading-[1.02] font-semibold tracking-[-0.05em] sm:text-5xl lg:text-6xl">
                Start with your voice. Find a clearer next step.
              </h2>
              <p className="mx-auto mt-6 max-w-xl text-base leading-7 text-slate-300 sm:text-lg">
                Begin a real-time conversation powered by Murf Falcon and LiveKit.
              </p>
              <button
                type="button"
                data-start-cta="final"
                onClick={handleStart}
                disabled={isStarting}
                className="mt-9 inline-flex h-14 items-center justify-center gap-2 rounded-full bg-white px-8 font-semibold text-[#111a2e] shadow-xl transition-all hover:-translate-y-0.5 hover:bg-violet-50 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-white disabled:pointer-events-none disabled:opacity-60 motion-reduce:transform-none"
              >
                {startLabel}
                <ArrowIcon className="size-5" />
              </button>
            </div>
          </Reveal>
        </section>
      </main>

      <footer className="px-5 py-9 sm:px-8 lg:px-10">
        <div className="mx-auto flex max-w-[1240px] flex-col gap-7 border-t border-slate-200 pt-8 md:flex-row md:items-end md:justify-between">
          <div>
            <a
              href="#home"
              className="inline-flex items-center gap-3 rounded-xl focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-violet-600"
            >
              <BrandMark className="size-9 rounded-xl" />
              <span className="text-lg font-bold tracking-[-0.03em] text-[#111a2e]">Aanya</span>
            </a>
            <p className="mt-4 max-w-xl text-sm leading-6 text-slate-500">
              Aanya provides general health information, not medical diagnosis or emergency care.
              Always consult a qualified healthcare professional for medical concerns.
            </p>
          </div>
          <div className="text-sm text-slate-500 md:text-right">
            <p>Murf Falcon + LiveKit</p>
            <p className="mt-1">© 2026 Aanya</p>
          </div>
        </div>
      </footer>
    </div>
  );
};
