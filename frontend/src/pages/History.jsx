import clsx from 'clsx'
import { History as HistoryIcon, Loader2, ScanSearch, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '@/api/client'
import { Card, EmptyState, SeverityChip, Spinner } from '@/components/Primitives'
import { phaseLabel, relativeTime, scoreColor } from '@/lib/format'

export default function History() {
  const [reviews, setReviews] = useState([])
  const [loading, setLoading] = useState(true)
  const [deleting, setDeleting] = useState(null)

  const load = useCallback(async () => {
    try {
      setReviews(await api.listReviews())
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    // Keep in-flight rows moving without opening an SSE stream per review.
    const timer = setInterval(load, 5_000)
    return () => clearInterval(timer)
  }, [load])

  async function remove(id, event) {
    event.preventDefault()
    event.stopPropagation()
    setDeleting(id)
    try {
      await api.deleteReview(id)
      setReviews((prev) => prev.filter((r) => r.id !== id))
    } finally {
      setDeleting(null)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-3 py-24 text-ink-400">
        <Spinner className="w-5 h-5" /> Loading history…
      </div>
    )
  }

  if (!reviews.length) {
    return (
      <EmptyState
        icon={<HistoryIcon className="w-10 h-10" />}
        title="No reviews yet"
        description="Point the reviewer at a git repository, a local folder, or a ZIP archive."
        action={
          <Link to="/" className="btn-primary">
            <ScanSearch className="w-4 h-4" /> Start a review
          </Link>
        }
      />
    )
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold tracking-tight">Review history</h1>
        <Link to="/" className="btn-primary"><ScanSearch className="w-4 h-4" /> New review</Link>
      </div>

      <div className="space-y-2">
        {reviews.map((r) => {
          const active = r.status === 'running' || r.status === 'queued'
          return (
            <Link key={r.id} to={`/reviews/${r.id}`} className="block group">
              <Card className="p-4 hover:border-ink-700 transition-colors">
                <div className="flex items-center gap-4">
                  <div className="w-12 text-center shrink-0">
                    {active ? (
                      <Loader2 className="w-5 h-5 mx-auto animate-spin text-accent" />
                    ) : (
                      <>
                        <div className={clsx('text-xl font-bold tabular-nums',
                          scoreColor(r.overall_score))}>
                          {r.overall_score ?? '—'}
                        </div>
                        {r.grade && <div className="text-[10px] text-ink-600">{r.grade}</div>}
                      </>
                    )}
                  </div>

                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium text-ink-100 truncate group-hover:text-white">
                        {r.display_name}
                      </span>
                      <span className="chip border-ink-700 bg-ink-850 text-ink-500">
                        {r.source_type}
                      </span>
                      {r.status === 'failed' && (
                        <span className="chip border-sev-critical/30 bg-sev-critical/10 text-sev-critical">
                          failed
                        </span>
                      )}
                      {r.status === 'cancelled' && (
                        <span className="chip border-sev-high/30 bg-sev-high/10 text-sev-high">
                          cancelled
                        </span>
                      )}
                    </div>
                    <div className="mono text-[11px] text-ink-600 truncate mt-0.5">
                      {r.source_ref}
                    </div>

                    {active && (
                      <div className="flex items-center gap-2 mt-2">
                        <div className="h-1 flex-1 max-w-[14rem] rounded-full bg-ink-800 overflow-hidden">
                          <div className="h-full bg-accent rounded-full transition-[width] duration-500"
                               style={{ width: `${Math.max(3, r.progress * 100)}%` }} />
                        </div>
                        <span className="text-[11px] text-ink-500">{phaseLabel(r.phase)}</span>
                      </div>
                    )}
                  </div>

                  <div className="hidden sm:flex items-center gap-3 shrink-0">
                    {r.critical_count > 0 && (
                      <SeverityChip severity="critical" count={r.critical_count} />
                    )}
                    {r.findings_count > 0 && (
                      <span className="text-xs text-ink-500">{r.findings_count} findings</span>
                    )}
                    <span className="text-xs text-ink-600 w-16 text-right">
                      {relativeTime(r.created_at)}
                    </span>
                  </div>

                  <button
                    onClick={(e) => remove(r.id, e)}
                    disabled={deleting === r.id}
                    aria-label={`Delete review of ${r.display_name}`}
                    className="shrink-0 p-2 rounded-lg text-ink-600 hover:text-sev-critical hover:bg-sev-critical/10 transition-colors"
                  >
                    {deleting === r.id
                      ? <Loader2 className="w-4 h-4 animate-spin" />
                      : <Trash2 className="w-4 h-4" />}
                  </button>
                </div>
              </Card>
            </Link>
          )
        })}
      </div>
    </div>
  )
}
