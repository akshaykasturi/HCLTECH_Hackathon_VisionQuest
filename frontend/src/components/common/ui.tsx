// Small shared building blocks: buttons, cards, badges, skeletons, empty and error states.
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { AlertTriangle, Inbox, Loader2, RotateCw } from "lucide-react";
import { describeError } from "../../lib/api";

const focusRing = "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary";

type Variant = "primary" | "secondary" | "ghost" | "danger";
const variants: Record<Variant, string> = {
  primary: "bg-primary text-white hover:bg-primary-hover disabled:bg-primary/50",
  secondary: "border border-slate-200 bg-white text-ink hover:bg-slate-50 disabled:text-muted",
  ghost: "text-ink hover:bg-slate-100 disabled:text-muted",
  danger: "bg-danger-text text-white hover:bg-red-800 disabled:bg-danger/50",
};

export function Button({ variant = "secondary", loading = false, loadingText, icon, children, className = "",
                         disabled, type = "button", ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; loading?: boolean; loadingText?: string; icon?: ReactNode }) {
  return (
    <button type={type} disabled={disabled || loading} aria-busy={loading || undefined}
            className={`inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed ${variants[variant]} ${focusRing} ${className}`}
            {...rest}>
      {loading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : icon}
      {loading && loadingText ? loadingText : children}
    </button>
  );
}

export function Card({ children, className = "", as: Tag = "section", ...rest }:
  { children: ReactNode; className?: string; as?: "section" | "div" | "article"; "aria-labelledby"?: string; id?: string }) {
  return <Tag className={`rounded-xl border border-slate-200 bg-white shadow-card ${className}`} {...rest}>{children}</Tag>;
}

export function CardHeader({ title, description, actions, icon, id }:
  { title: ReactNode; description?: ReactNode; actions?: ReactNode; icon?: ReactNode; id?: string }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
      <div className="flex min-w-0 items-start gap-3">
        {icon && <span className="mt-0.5 text-primary" aria-hidden>{icon}</span>}
        <div className="min-w-0">
          <h2 id={id} className="text-base font-semibold text-ink">{title}</h2>
          {description && <p className="mt-0.5 text-sm text-muted">{description}</p>}
        </div>
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export type Tone = "neutral" | "primary" | "success" | "warning" | "danger";
const tones: Record<Tone, string> = {
  neutral: "bg-slate-100 text-slate-700 ring-slate-200",
  primary: "bg-primary-soft text-primary ring-primary/20",
  success: "bg-success-soft text-success-text ring-success/30",
  warning: "bg-warning-soft text-warning-text ring-warning/30",
  danger: "bg-danger-soft text-danger-text ring-danger/30",
};

export function Badge({ tone = "neutral", children, title, icon, className = "" }:
  { tone?: Tone; children: ReactNode; title?: string; icon?: ReactNode; className?: string }) {
  return (
    <span title={title} className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${tones[tone]} ${className}`}>
      {icon}
      {children}
    </span>
  );
}

export function Mono({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <code className={`break-all font-mono text-[0.8125rem] text-slate-700 ${className}`}>{children}</code>;
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div aria-hidden className={`animate-pulse rounded-md bg-slate-200/80 ${className}`} />;
}

export function LoadingBlock({ label, rows = 3 }: { label: string; rows?: number }) {
  return (
    <div role="status" aria-live="polite" className="space-y-3 p-5">
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, i) => <Skeleton key={i} className={`h-4 ${i % 2 ? "w-2/3" : "w-full"}`} />)}
    </div>
  );
}

export function EmptyState({ title, children, icon, action }:
  { title: string; children?: ReactNode; icon?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center px-6 py-10 text-center">
      <span className="mb-3 rounded-full bg-slate-100 p-3 text-muted" aria-hidden>{icon ?? <Inbox className="h-5 w-5" />}</span>
      <p className="font-medium text-ink">{title}</p>
      {children && <div className="mt-1 max-w-md text-sm text-muted">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** An API error with a clear title, the message and (when it makes sense) a retry button. Announced to screen readers. */
export function ErrorAlert({ error, onRetry, retrying = false, compact = false }:
  { error: unknown; onRetry?: () => void; retrying?: boolean; compact?: boolean }) {
  const info = describeError(error);
  return (
    <div role="alert" className={`flex flex-wrap items-start gap-3 rounded-lg border border-danger/30 bg-danger-soft ${compact ? "p-3" : "p-4"}`}>
      <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-danger-text" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="font-semibold text-danger-text">{info.title}</p>
        <p className="mt-0.5 text-sm text-slate-700">{info.message}</p>
      </div>
      {onRetry && info.retryable && (
        <Button onClick={onRetry} loading={retrying} icon={<RotateCw className="h-4 w-4" aria-hidden />}>Retry</Button>
      )}
    </div>
  );
}

export function PageHeader({ title, description, actions, eyebrow }:
  { title: string; description?: ReactNode; actions?: ReactNode; eyebrow?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {eyebrow && <div className="mb-1">{eyebrow}</div>}
        <h1 className="text-2xl font-semibold tracking-tight text-ink">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
