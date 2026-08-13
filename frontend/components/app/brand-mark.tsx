import { cn } from '@/lib/shadcn/utils';

/** The Aanya waveform badge. Shared by the landing page and the internal dashboards. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        'aanya-brand-mark inline-grid size-10 shrink-0 place-items-center rounded-[14px]',
        className
      )}
      aria-hidden="true"
    >
      <svg viewBox="0 0 32 32" className="size-6" fill="none">
        <path
          d="M7 17.25h3.2l2.05-6.5 3.45 11.5 2.55-8 1.5 3h5.25"
          stroke="currentColor"
          strokeWidth="2.25"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  );
}
