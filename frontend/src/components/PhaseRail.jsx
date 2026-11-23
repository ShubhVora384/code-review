import clsx from 'clsx'
import { Check } from 'lucide-react'

import { PHASE_SEQUENCE, phaseLabel } from '@/lib/format'

/** Horizontal stepper showing where the pipeline currently is. */
export default function PhaseRail({ phase, status, progress }) {
  const failed = status === 'failed' || status === 'cancelled'
  const currentIndex = PHASE_SEQUENCE.indexOf(phase)
  const done = status === 'completed'

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm text-ink-200">
          {done ? 'Review complete' : failed ? phaseLabel(status) : phaseLabel(phase)}
        </span>
        <span className="text-xs tabular-nums text-ink-500">
          {Math.round((done ? 1 : progress) * 100)}%
        </span>
      </div>

      <div className="h-1 rounded-full bg-ink-800 overflow-hidden mb-4">
        <div
          className={clsx('h-full rounded-full transition-[width] duration-500 ease-out',
            failed ? 'bg-sev-critical' : done ? 'bg-good' : 'bg-accent')}
          style={{ width: `${Math.max(2, (done ? 1 : progress) * 100)}%` }}
        />
      </div>

      <ol className="grid grid-cols-2 sm:grid-cols-4 gap-x-3 gap-y-2">
        {PHASE_SEQUENCE.map((p, i) => {
          const complete = done || (currentIndex > -1 && i < currentIndex)
          const active = !done && i === currentIndex
          return (
            <li key={p} className="flex items-center gap-2 min-w-0">
              <span className={clsx(
                'shrink-0 w-4 h-4 rounded-full grid place-items-center text-[9px] font-bold',
                complete ? 'bg-good/20 text-good'
                  : active ? 'bg-accent text-ink-950'
                  : 'bg-ink-800 text-ink-600',
              )}>
                {complete ? <Check className="w-2.5 h-2.5" strokeWidth={3} /> : i + 1}
              </span>
              <span className={clsx('text-[11px] truncate',
                active ? 'text-ink-100 font-medium'
                  : complete ? 'text-ink-400' : 'text-ink-600')}>
                {phaseLabel(p)}
              </span>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
