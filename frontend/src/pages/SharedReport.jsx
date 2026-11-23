import clsx from 'clsx'
import { AlertCircle, BadgeCheck, ScanSearch } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'

import { api } from '@/api/client'
import LaunchVerdict from '@/components/LaunchVerdict'
import { Card, EmptyState, ScoreBar, ScoreDial, SectionTitle, SeverityChip, Spinner } from '@/components/Primitives'
import { SEVERITY_META, SEVERITY_ORDER, scoreColor } from '@/lib/format'

/**
 * The public view of a report.
 *
 * Deliberately not the engineer view: no source code, no proof scripts, no
 * file paths on disk. Someone holding this link was sent a result, not access
 * to the codebase.
 */
export default function SharedReport() {
  const { token } = useParams()
  const [report, setReport] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.getShared(token).then(setReport).catch((e) => setError(e.message))
  }, [token])

  if (error) {
    return (
      <div className="min-h-screen grid place-items-center px-5">
        <EmptyState
          icon={<AlertCircle className="w-10 h-10" />}
          title="This report is not available"
          description="The link may have been revoked, or it never existed."
        />
      </div>
    )
  }

  if (!report) {
    return (
      <div className="min-h-screen flex items-center justify-center gap-3 text-ink-400">
        <Spinner className="w-5 h-5" /> Loading report…
      </div>
    )
  }

  const proven = report.proven_findings ?? []
  const others = report.other_findings ?? []
  const recs = report.recommendations ?? []

  return (
    <div className="min-h-screen bg-ink-950">
      <header className="border-b border-ink-850">
        <div className="mx-auto max-w-4xl px-5 h-14 flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-lg bg-accent grid place-items-center">
            <ScanSearch className="w-4 h-4 text-ink-950" strokeWidth={2.5} />
          </div>
          <span className="font-semibold tracking-tight">Code Audit</span>
          <span className="ml-auto text-xs text-ink-500">
            {report.finished_at
              ? new Date(report.finished_at).toLocaleDateString()
              : ''}
          </span>
        </div>
      </header>

      <main className="mx-auto max-w-4xl px-5 py-10 space-y-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{report.display_name}</h1>
          <p className="text-sm text-ink-500 mt-1">Independent automated security audit</p>
        </div>

        <LaunchVerdict verdict={report.launch_verdict} provenCount={proven.length} />

        <div className="grid sm:grid-cols-[auto,1fr] gap-6">
          <Card className="card-pad flex flex-col items-center justify-center gap-2 sm:w-52">
            <ScoreDial score={report.overall_score} size={124} label={report.grade} />
            <p className={clsx('text-sm font-medium text-center', scoreColor(report.overall_score))}>
              {report.verdict}
            </p>
          </Card>

          <Card className="card-pad">
            <SectionTitle title="Scores by area" />
            <div className="grid sm:grid-cols-2 gap-x-8 gap-y-4">
              {Object.entries(report.dimensions ?? {}).map(([dim, value]) => (
                <ScoreBar key={dim} label={dim} value={value} />
              ))}
            </div>
            <div className="flex flex-wrap gap-1.5 mt-5 pt-4 border-t border-ink-850">
              {SEVERITY_ORDER.filter((s) => report.severity_counts?.[s]).map((s) => (
                <SeverityChip key={s} severity={s} count={report.severity_counts[s]} />
              ))}
            </div>
          </Card>
        </div>

        {report.summary && (
          <Card className="card-pad">
            <SectionTitle title="Summary" />
            <p className="text-sm text-ink-300 leading-relaxed whitespace-pre-line">
              {report.summary}
            </p>
          </Card>
        )}

        {proven.length > 0 && (
          <Card className="card-pad border-sev-critical/30">
            <SectionTitle
              icon={<BadgeCheck className="w-4 h-4" />}
              title={`Demonstrated vulnerabilities (${proven.length})`}
              subtitle="Each was reproduced against the code, not merely suspected"
            />
            <div className="space-y-3">
              {proven.map((f, i) => <PublicFinding key={i} finding={f} proven />)}
            </div>
          </Card>
        )}

        {others.length > 0 && (
          <Card className="card-pad">
            <SectionTitle
              title={`Other findings (${others.length})`}
              subtitle="Identified but not automatically demonstrated"
            />
            <div className="space-y-3">
              {others.slice(0, 25).map((f, i) => <PublicFinding key={i} finding={f} />)}
            </div>
          </Card>
        )}

        {recs.length > 0 && (
          <Card className="card-pad">
            <SectionTitle title="What to do next" />
            <div className="space-y-3">
              {recs.map((r, i) => (
                <div key={i} className="rounded-lg bg-ink-850 border border-ink-800 p-3.5">
                  <div className="flex items-start justify-between gap-3">
                    <h4 className="text-sm font-medium text-ink-100">{r.title}</h4>
                    <span className="chip border-ink-700 bg-ink-800 text-ink-400 shrink-0">
                      {r.effort} effort
                    </span>
                  </div>
                  <p className="text-sm text-ink-400 mt-1.5">{r.detail}</p>
                </div>
              ))}
            </div>
          </Card>
        )}

        <footer className="text-xs text-ink-600 pt-4 border-t border-ink-850">
          {report.metrics?.files_reviewed} of {report.metrics?.files_indexed} files
          reviewed · {(report.metrics?.total_loc ?? 0).toLocaleString()} lines of code.
          This report shows findings only; no source code is exposed.
        </footer>
      </main>
    </div>
  )
}

function PublicFinding({ finding, proven = false }) {
  const meta = SEVERITY_META[finding.severity] ?? SEVERITY_META.info
  return (
    <div className={clsx('rounded-lg border p-3.5', proven ? meta.border : 'border-ink-800')}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className={clsx('chip', meta.bg, meta.border, meta.color)}>{meta.label}</span>
        <span className="chip border-ink-700 bg-ink-850 text-ink-400">{finding.category}</span>
        {proven && (
          <span className="chip border-sev-critical/40 bg-sev-critical/15 text-sev-critical font-semibold">
            <BadgeCheck className="w-3 h-3" /> proven
          </span>
        )}
      </div>
      <h4 className="text-sm font-medium text-ink-100 mt-2">{finding.title}</h4>
      {finding.path && (
        <p className="mono text-[11px] text-ink-600 mt-0.5">
          {finding.path}{finding.line_start ? `:${finding.line_start}` : ''}
        </p>
      )}
      {proven && finding.proof_evidence && (
        <p className="text-sm text-ink-200 mt-2 leading-relaxed border-l-2 border-sev-critical/40 pl-3">
          {finding.proof_evidence}
        </p>
      )}
      {finding.description && (
        <p className="text-sm text-ink-400 mt-2 leading-relaxed">{finding.description}</p>
      )}
      {finding.suggestion && (
        <p className="text-sm text-ink-300 mt-2">
          <span className="text-ink-500">Fix: </span>{finding.suggestion}
        </p>
      )}
    </div>
  )
}
