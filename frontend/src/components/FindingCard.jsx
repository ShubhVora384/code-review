import clsx from 'clsx'
import { BadgeCheck, ChevronDown, Lightbulb, ShieldCheck, Zap } from 'lucide-react'
import { useState } from 'react'

import { SEVERITY_META } from '@/lib/format'

export default function FindingCard({ finding, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen)
  const meta = SEVERITY_META[finding.severity]

  const location = finding.line_start
    ? `${finding.path ?? ''}:${finding.line_start}${
        finding.line_end && finding.line_end !== finding.line_start ? `-${finding.line_end}` : ''
      }`
    : finding.path ?? 'system-wide'

  return (
    <div className={clsx('rounded-lg border overflow-hidden transition-colors',
      meta.border, open ? 'bg-ink-850' : 'bg-ink-900 hover:bg-ink-850')}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-start gap-3 p-3.5 text-left"
        aria-expanded={open}
      >
        <span className={clsx('shrink-0 mt-1 w-2 h-2 rounded-full', meta.color, 'bg-current')} />
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2 flex-wrap">
            <span className={clsx('chip', meta.bg, meta.border, meta.color)}>{meta.label}</span>
            <span className="chip border-ink-700 bg-ink-800 text-ink-400">{finding.category}</span>
            {finding.proof_status === 'proven' ? (
              <span
                className="chip border-sev-critical/40 bg-sev-critical/15 text-sev-critical font-semibold"
                title={finding.proof_executed
                  ? 'Reproduced by running it against your code'
                  : 'Traced deterministically in your code'}
              >
                <BadgeCheck className="w-3 h-3" /> proven
              </span>
            ) : finding.verified && (
              <span className="chip border-good/30 bg-good/10 text-good">
                <ShieldCheck className="w-3 h-3" /> verified
              </span>
            )}
            <span className="text-[11px] text-ink-500 tabular-nums">
              {Math.round(finding.confidence * 100)}% confidence
            </span>
          </span>
          <span className="block text-sm font-medium text-ink-100 mt-1.5">{finding.title}</span>
          <span className="block text-[11px] mono text-ink-500 mt-1 truncate">{location}</span>
        </span>
        <ChevronDown className={clsx('shrink-0 w-4 h-4 text-ink-500 transition-transform mt-1',
          open && 'rotate-180')} />
      </button>

      {open && (
        <div className="px-3.5 pb-4 pt-0 space-y-3 animate-fade-up">
          {finding.proof_status === 'proven' && finding.proof_evidence && (
            <div className="rounded-lg border border-sev-critical/30 bg-sev-critical/5 p-3">
              <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-sev-critical mb-1.5">
                <BadgeCheck className="w-3.5 h-3.5" />
                {finding.proof_executed ? 'Reproduced by running it' : 'Traced in your code'}
              </div>
              <p className="text-sm text-ink-200 leading-relaxed">{finding.proof_evidence}</p>
            </div>
          )}

          {finding.proof_status === 'inconclusive' && (
            <p className="text-xs text-ink-500 italic">
              This one could not be demonstrated automatically
              {finding.proof_summary ? ` — ${finding.proof_summary}` : ''}. Worth a
              human look before acting on it.
            </p>
          )}

          <p className="text-sm text-ink-300 leading-relaxed">{finding.description}</p>

          {finding.code_snippet && (
            <pre className="mono bg-ink-950 border border-ink-800 rounded-lg p-3 overflow-x-auto text-ink-300">
              <code>{finding.code_snippet}</code>
            </pre>
          )}

          {finding.impact && (
            <div className="flex gap-2.5 text-sm">
              <Zap className="w-4 h-4 shrink-0 mt-0.5 text-sev-high" />
              <p className="text-ink-300">
                <span className="text-ink-500">Impact: </span>{finding.impact}
              </p>
            </div>
          )}

          {finding.suggestion && (
            <div className="flex gap-2.5 text-sm">
              <Lightbulb className="w-4 h-4 shrink-0 mt-0.5 text-good" />
              <p className="text-ink-300">
                <span className="text-ink-500">Fix: </span>{finding.suggestion}
              </p>
            </div>
          )}

          {finding.suggested_patch && (
            <div>
              <div className="text-[11px] uppercase tracking-wider text-ink-500 mb-1.5">
                Proposed change
              </div>
              <pre className="mono bg-ink-950 border border-good/20 rounded-lg p-3 overflow-x-auto text-good/90">
                <code>{finding.suggested_patch}</code>
              </pre>
            </div>
          )}

          {finding.proof_output && (
            <details className="group">
              <summary className="text-xs text-ink-500 hover:text-ink-300 cursor-pointer">
                Show what the check printed
              </summary>
              <pre className="mono text-[11px] bg-ink-950 border border-ink-800 rounded-lg p-3 mt-2 overflow-x-auto text-ink-400 whitespace-pre-wrap">
                {finding.proof_output}
              </pre>
            </details>
          )}

          {finding.critic_note && (
            <p className="text-xs text-ink-500 italic border-l-2 border-ink-700 pl-3">
              Critic: {finding.critic_note}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
