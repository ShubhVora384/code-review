import clsx from 'clsx'
import { ChevronDown, FileCode2 } from 'lucide-react'
import { useState } from 'react'

import { SEVERITY_ORDER, dirName, fileName, scoreColor } from '@/lib/format'
import FindingCard from './FindingCard'

export default function FileReportCard({ file }) {
  const [open, setOpen] = useState(false)

  const counts = SEVERITY_ORDER
    .map((sev) => ({ sev, n: file.findings.filter((f) => f.severity === sev).length }))
    .filter((c) => c.n > 0)

  return (
    <div className="card overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-3.5 p-4 text-left hover:bg-ink-850 transition-colors"
        aria-expanded={open}
      >
        <FileCode2 className="w-4 h-4 shrink-0 text-ink-500" />

        <span className="min-w-0 flex-1">
          <span className="flex items-baseline gap-2">
            <span className="text-sm font-medium text-ink-100 truncate">{fileName(file.path)}</span>
            <span className="text-[11px] mono text-ink-600 truncate hidden sm:inline">
              {dirName(file.path)}
            </span>
          </span>
          <span className="flex items-center gap-2 mt-1 flex-wrap">
            {file.language && <span className="text-[11px] text-ink-500">{file.language}</span>}
            <span className="text-[11px] text-ink-600">{file.loc} loc</span>
            {counts.map(({ sev, n }) => (
              <span key={sev} className={clsx('text-[11px]', `text-sev-${sev}`)}>{n} {sev}</span>
            ))}
            {file.status !== 'reviewed' && (
              <span className="chip border-ink-700 bg-ink-800 text-ink-400">{file.status}</span>
            )}
          </span>
        </span>

        <span className={clsx('text-lg font-bold tabular-nums shrink-0', scoreColor(file.score))}>
          {file.score ?? '—'}
        </span>
        <ChevronDown className={clsx('w-4 h-4 shrink-0 text-ink-500 transition-transform',
          open && 'rotate-180')} />
      </button>

      {open && (
        <div className="border-t border-ink-800 p-4 space-y-4 animate-fade-up">
          {file.skip_reason && <p className="text-sm text-ink-400 italic">{file.skip_reason}</p>}
          {file.role && (
            <p className="text-sm text-ink-300">
              <span className="text-ink-500">Role: </span>{file.role}
            </p>
          )}
          {file.summary && <p className="text-sm text-ink-300 leading-relaxed">{file.summary}</p>}

          {file.dimension_scores && Object.keys(file.dimension_scores).length > 0 && (
            <div className="flex flex-wrap gap-2">
              {Object.entries(file.dimension_scores).map(([dim, value]) => (
                <span key={dim} className="chip border-ink-700 bg-ink-850">
                  <span className="text-ink-400 capitalize">{dim}</span>
                  <span className={clsx('font-semibold', scoreColor(value))}>
                    {Math.round(value)}
                  </span>
                </span>
              ))}
            </div>
          )}

          {!!file.strengths?.length && (
            <div>
              <div className="text-[11px] uppercase tracking-wider text-ink-500 mb-2">Strengths</div>
              <ul className="space-y-1">
                {file.strengths.map((s, i) => (
                  <li key={i} className="text-sm text-ink-300 flex gap-2">
                    <span className="text-good shrink-0">+</span>{s}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {file.findings.length > 0 ? (
            <div>
              <div className="text-[11px] uppercase tracking-wider text-ink-500 mb-2">
                {file.findings.length} finding{file.findings.length === 1 ? '' : 's'}
              </div>
              <div className="space-y-2">
                {file.findings.map((f) => <FindingCard key={f.id} finding={f} />)}
              </div>
            </div>
          ) : (
            file.status === 'reviewed' && (
              <p className="text-sm text-good/80">No issues found in this file.</p>
            )
          )}
        </div>
      )}
    </div>
  )
}
