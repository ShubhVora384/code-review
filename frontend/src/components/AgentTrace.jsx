import clsx from 'clsx'
import {
  AlertTriangle, Boxes, Brain, CheckCircle2, FileSearch, GitBranch, KeyRound,
  ListChecks, Network, ScanLine, ShieldQuestion, Sparkles, XCircle,
} from 'lucide-react'
import { useEffect, useRef } from 'react'

import { Spinner } from './Primitives'

const ICONS = {
  review_started: GitBranch,
  source_ready: GitBranch,
  inventory_ready: Boxes,
  phase: ScanLine,
  profile_ready: Sparkles,
  structure_ready: Network,
  plan_ready: ListChecks,
  review_plan_ready: ListChecks,
  progress: FileSearch,
  files_reviewed: FileSearch,
  cross_cutting_ready: Network,
  critique_complete: ShieldQuestion,
  budget_exhausted: AlertTriangle,
  key_rotated: KeyRound,
  review_completed: CheckCircle2,
  review_failed: XCircle,
  review_cancelled: XCircle,
}

const TONE = {
  review_completed: 'text-good',
  review_failed: 'text-sev-critical',
  review_cancelled: 'text-sev-high',
  budget_exhausted: 'text-sev-high',
  key_rotated: 'text-sev-medium',
  phase: 'text-accent',
}

/** Events that carry no information a human wants in a timeline. */
const HIDDEN = new Set(['agent_start', 'agent_done'])

export default function AgentTrace({ events, live }) {
  const endRef = useRef(null)
  const visible = events.filter((e) => !HIDDEN.has(e.type))

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [visible.length])

  if (!visible.length) {
    return (
      <div className="flex items-center gap-3 text-sm text-ink-400 py-8 justify-center">
        <Spinner className="w-4 h-4" />
        Waiting for the first agent turn…
      </div>
    )
  }

  return (
    <div className="relative max-h-[28rem] overflow-y-auto pr-2">
      <div className="absolute left-[15px] top-2 bottom-2 w-px bg-ink-800" />
      <ol className="space-y-3">
        {visible.map((event) => {
          const Icon = ICONS[event.type] ?? Brain
          const tone = TONE[event.type] ?? 'text-ink-400'
          return (
            <li key={event.seq} className="relative flex gap-3 animate-fade-up">
              <div className={clsx(
                'relative z-10 shrink-0 w-8 h-8 rounded-lg grid place-items-center',
                'bg-ink-850 border border-ink-750', tone,
              )}>
                <Icon className="w-4 h-4" />
              </div>
              <div className="min-w-0 flex-1 pt-1">
                <div className="flex items-baseline gap-2 flex-wrap">
                  {event.agent && (
                    <span className="text-[11px] font-semibold uppercase tracking-wider text-accent-glow">
                      {event.agent}
                    </span>
                  )}
                  <span className="text-sm text-ink-200">{event.message}</span>
                </div>
                <EventDetail event={event} />
              </div>
            </li>
          )
        })}
      </ol>

      {live && (
        <div className="flex items-center gap-2 text-xs text-ink-500 mt-4 ml-11">
          <span className="relative flex w-2 h-2">
            <span className="absolute inline-flex w-full h-full rounded-full bg-accent animate-pulse-ring" />
            <span className="relative inline-flex w-2 h-2 rounded-full bg-accent" />
          </span>
          Streaming…
        </div>
      )}
      <div ref={endRef} />
    </div>
  )
}

function EventDetail({ event }) {
  const p = event.payload ?? {}

  if (event.type === 'inventory_ready' && p.languages) {
    return (
      <div className="flex flex-wrap gap-1.5 mt-1.5">
        {Object.entries(p.languages).slice(0, 6).map(([lang, d]) => (
          <span key={lang} className="chip border-ink-750 bg-ink-850 text-ink-400">
            {lang} <span className="opacity-60">{d.loc.toLocaleString()} loc</span>
          </span>
        ))}
      </div>
    )
  }

  if (event.type === 'profile_ready') {
    return (
      <div className="mt-1.5 text-xs text-ink-400 space-y-1">
        <div>
          <span className="text-ink-500">Type:</span> {p.project_type}
          {' · '}
          <span className="text-ink-500">Domain:</span> {p.domain}
        </div>
        {!!p.frameworks?.length && (
          <div className="flex flex-wrap gap-1.5">
            {p.frameworks.slice(0, 8).map((f) => (
              <span key={f} className="chip border-accent/25 bg-accent/10 text-accent-glow">{f}</span>
            ))}
          </div>
        )}
      </div>
    )
  }

  if (event.type === 'progress' && p.path) {
    return (
      <div className="mt-1 text-xs text-ink-500 mono truncate">
        {p.path}
        {p.score != null && <span className="ml-2 text-ink-400">score {p.score}</span>}
        {!!p.findings && <span className="ml-2 text-sev-medium">{p.findings} finding(s)</span>}
      </div>
    )
  }

  if (event.type === 'critique_complete') {
    return (
      <div className="mt-1 text-xs text-ink-500">
        <span className="text-good">{p.kept} kept</span>
        {' · '}
        <span className="text-sev-high">{p.rejected} discarded</span>
      </div>
    )
  }

  if (event.type === 'key_rotated') {
    return (
      <div className="mt-1 text-xs text-ink-500">
        {p.from_key} hit a rate limit → switched to {p.to_key}
        {p.cooldown_s ? ` (cooling ${p.cooldown_s}s)` : ''}
      </div>
    )
  }

  if (event.type === 'review_completed') {
    return (
      <div className="mt-1 text-xs text-ink-400">
        Score {p.overall_score} ({p.grade}) · {p.findings} findings ·{' '}
        {Number(p.tokens ?? 0).toLocaleString()} tokens
      </div>
    )
  }

  return null
}
