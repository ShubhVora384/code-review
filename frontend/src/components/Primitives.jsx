import clsx from 'clsx'

import { SEVERITY_META, scoreColor, scoreStroke } from '@/lib/format'

export function Card({ className, children }) {
  return <div className={clsx('card', className)}>{children}</div>
}

export function SectionTitle({ icon, title, subtitle, action }) {
  return (
    <div className="flex items-start justify-between gap-4 mb-4">
      <div className="flex items-start gap-3">
        {icon && <div className="mt-0.5 text-accent">{icon}</div>}
        <div>
          <h2 className="text-base font-semibold text-ink-100">{title}</h2>
          {subtitle && <p className="text-sm text-ink-400 mt-0.5">{subtitle}</p>}
        </div>
      </div>
      {action}
    </div>
  )
}

export function SeverityChip({ severity, count }) {
  const meta = SEVERITY_META[severity]
  return (
    <span className={clsx('chip', meta.bg, meta.border, meta.color)}>
      <span className="w-1.5 h-1.5 rounded-full bg-current" />
      {meta.label}
      {count !== undefined && <span className="opacity-70">{count}</span>}
    </span>
  )
}

export function Tag({ children, className }) {
  return (
    <span className={clsx('chip border-ink-700 bg-ink-850 text-ink-300', className)}>
      {children}
    </span>
  )
}

/** Circular score dial. `size` drives the whole thing so it scales cleanly. */
export function ScoreDial({ score, size = 120, label }) {
  const stroke = size / 12
  const radius = (size - stroke) / 2
  const circumference = 2 * Math.PI * radius
  const pct = Math.max(0, Math.min(100, score ?? 0)) / 100

  return (
    <div className="relative inline-flex items-center justify-center"
         style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none"
                stroke="#1d222d" strokeWidth={stroke} />
        <circle
          cx={size / 2} cy={size / 2} r={radius} fill="none"
          stroke={scoreStroke(score)} strokeWidth={stroke} strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - pct)}
          style={{ transition: 'stroke-dashoffset .9s cubic-bezier(.4,0,.2,1)' }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className={clsx('font-bold tabular-nums', scoreColor(score))}
              style={{ fontSize: size / 3.4 }}>
          {score == null ? '—' : Math.round(score)}
        </span>
        {label && (
          <span className="text-[10px] uppercase tracking-wider text-ink-500 mt-0.5">{label}</span>
        )}
      </div>
    </div>
  )
}

export function ScoreBar({ label, value, weight }) {
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1.5">
        <span className="text-sm text-ink-200 capitalize">{label}</span>
        <span className="flex items-baseline gap-2">
          {weight !== undefined && (
            <span className="text-[10px] text-ink-500">{Math.round(weight * 100)}% weight</span>
          )}
          <span className={clsx('text-sm font-semibold tabular-nums', scoreColor(value))}>
            {Math.round(value)}
          </span>
        </span>
      </div>
      <div className="h-1.5 rounded-full bg-ink-800 overflow-hidden">
        <div
          className="h-full rounded-full transition-[width] duration-700 ease-out"
          style={{ width: `${Math.max(2, value)}%`, background: scoreStroke(value) }}
        />
      </div>
    </div>
  )
}

export function EmptyState({ icon, title, description, action }) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-16 px-6">
      {icon && <div className="text-ink-600 mb-4">{icon}</div>}
      <h3 className="text-ink-200 font-medium">{title}</h3>
      {description && <p className="text-sm text-ink-500 mt-1.5 max-w-md">{description}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}

export function Spinner({ className }) {
  return (
    <svg className={clsx('animate-spin', className)} viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" opacity="0.2" />
      <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  )
}
