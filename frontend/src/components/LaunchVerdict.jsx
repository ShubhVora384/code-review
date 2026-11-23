import clsx from 'clsx'
import { ShieldAlert, ShieldCheck } from 'lucide-react'

import { LAUNCH_META } from '@/lib/format'

/**
 * The one-line answer a founder came for.
 *
 * Driven by what was *demonstrated*, not by what was suspected — telling
 * someone their product is unsafe to ship should require evidence.
 */
export default function LaunchVerdict({ verdict, provenCount }) {
  if (!verdict) return null
  const meta = LAUNCH_META[verdict.decision] ?? LAUNCH_META.fix_first
  const blocking = verdict.decision === 'do_not_launch'

  return (
    <div className={clsx('rounded-xl border p-5 flex items-start gap-4', meta.tone)}>
      <div className={clsx('shrink-0 mt-0.5', meta.color)}>
        {blocking
          ? <ShieldAlert className="w-7 h-7" />
          : <ShieldCheck className="w-7 h-7" />}
      </div>
      <div className="min-w-0">
        <h2 className={clsx('text-lg font-semibold', meta.color)}>
          {meta.icon} {verdict.headline}
        </h2>
        <p className="text-sm text-ink-200 mt-1.5 leading-relaxed">{verdict.reason}</p>
        {provenCount > 0 && (
          <p className="text-xs text-ink-400 mt-2">
            {provenCount} finding{provenCount === 1 ? ' was' : 's were'} reproduced
            against your code, so {provenCount === 1 ? 'it is' : 'they are'} not a
            false alarm.
          </p>
        )}
      </div>
    </div>
  )
}
