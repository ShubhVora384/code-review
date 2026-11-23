import clsx from 'clsx'
import {
  AlertCircle, ArrowLeft, BadgeCheck, Ban, Boxes, Check, ChevronDown, Download,
  FileCode2, Gauge, Layers, Lightbulb, Link2, Network, Sparkles, Target,
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { api } from '@/api/client'
import AgentTrace from '@/components/AgentTrace'
import FileReportCard from '@/components/FileReportCard'
import FindingCard from '@/components/FindingCard'
import LaunchVerdict from '@/components/LaunchVerdict'
import PhaseRail from '@/components/PhaseRail'
import {
  Card, EmptyState, ScoreBar, ScoreDial, SectionTitle, SeverityChip, Spinner, Tag,
} from '@/components/Primitives'
import { useReviewStream } from '@/hooks/useReviewStream'
import { SEVERITY_ORDER, compact, duration, scoreColor } from '@/lib/format'

export default function ReviewPage() {
  const { id } = useParams()
  const { review, events, connected, loading, error, refresh } = useReviewStream(id)
  const [panel, setPanel] = useState('overview')
  const [severityFilter, setSeverityFilter] = useState('all')
  const [cancelling, setCancelling] = useState(false)
  const [shareUrl, setShareUrl] = useState(null)
  const [copied, setCopied] = useState(false)

  const running = review?.status === 'running' || review?.status === 'queued'

  const allFindings = useMemo(() => {
    if (!review) return []
    const fromFiles = review.files.flatMap((f) => f.findings)
    return [...review.cross_cutting_findings, ...fromFiles].sort(
      (a, b) => SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity),
    )
  }, [review])

  const filtered = severityFilter === 'all'
    ? allFindings
    : allFindings.filter((f) => f.severity === severityFilter)

  const proven = allFindings.filter((f) => f.proof_status === 'proven')

  async function share() {
    if (!id) return
    const res = await api.enableShare(id)
    const url = `${window.location.origin}/shared/${res.share_token}`
    setShareUrl(url)
    try {
      await navigator.clipboard.writeText(url)
      setCopied(true)
      setTimeout(() => setCopied(false), 2500)
    } catch {
      /* clipboard blocked; the link is shown regardless */
    }
  }

  async function cancel() {
    if (!id) return
    setCancelling(true)
    try {
      await api.cancelReview(id)
      await refresh()
    } finally {
      setCancelling(false)
    }
  }

  if (loading && !review) {
    return (
      <div className="flex items-center justify-center gap-3 py-24 text-ink-400">
        <Spinner className="w-5 h-5" /> Loading review…
      </div>
    )
  }

  if (error && !review) {
    return (
      <EmptyState
        icon={<AlertCircle className="w-10 h-10" />}
        title="Review not found"
        description={error}
        action={<Link to="/" className="btn-primary">Start a new review</Link>}
      />
    )
  }

  if (!review) return null

  const scores = review.scores
  const narrative = scores?.narrative
  const profile = review.profile

  return (
    <div className="space-y-6">
      {/* header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <Link to="/reviews"
                className="inline-flex items-center gap-1.5 text-xs text-ink-500 hover:text-ink-300 mb-2">
            <ArrowLeft className="w-3.5 h-3.5" /> All reviews
          </Link>
          <h1 className="text-2xl font-semibold tracking-tight truncate">{review.display_name}</h1>
          <div className="flex flex-wrap items-center gap-2 mt-2">
            <Tag>{review.source_type}</Tag>
            {review.branch && <Tag>{review.branch}</Tag>}
            {review.commit_sha && <Tag className="mono">{review.commit_sha.slice(0, 8)}</Tag>}
            <StatusChip status={review.status} />
            {duration(review.started_at, review.finished_at) && (
              <span className="text-xs text-ink-500">
                took {duration(review.started_at, review.finished_at)}
              </span>
            )}
          </div>
          <p className="mono text-[11px] text-ink-600 mt-2 truncate max-w-xl">{review.source_ref}</p>
        </div>

        <div className="flex items-center gap-2">
          {running && (
            <button onClick={cancel} disabled={cancelling} className="btn-danger">
              <Ban className="w-4 h-4" /> {cancelling ? 'Cancelling…' : 'Cancel'}
            </button>
          )}
          {review.status === 'completed' && (
            <>
              <button onClick={share} className="btn-ghost">
                {copied ? <Check className="w-4 h-4 text-good" /> : <Link2 className="w-4 h-4" />}
                {copied ? 'Link copied' : 'Share'}
              </button>
              <a href={api.reportUrl(review.id)} download className="btn-ghost">
                <Download className="w-4 h-4" /> Markdown
              </a>
            </>
          )}
        </div>
      </div>

      {/* failure */}
      {review.error && review.status !== 'cancelled' && (
        <FailureBanner error={review.error} events={events} />
      )}

      {/* live progress */}
      {running && (
        <Card className="card-pad space-y-5">
          <PhaseRail phase={review.phase} status={review.status} progress={review.progress} />
          <div className="border-t border-ink-850 pt-4">
            <AgentTrace events={events} live={connected} />
          </div>
        </Card>
      )}

      {scores?.launch_verdict && (
        <LaunchVerdict
          verdict={scores.launch_verdict}
          provenCount={scores.proven_findings ?? proven.length}
        />
      )}

      {shareUrl && (
        <div className="flex items-center gap-3 rounded-lg border border-accent/30 bg-accent/5 p-3.5">
          <Link2 className="w-4 h-4 text-accent shrink-0" />
          <span className="mono text-[11px] text-ink-300 truncate flex-1">{shareUrl}</span>
          <span className="text-[11px] text-ink-500 shrink-0">
            Public, findings only — no source code
          </span>
        </div>
      )}

      {/* scorecard */}
      {scores && (
        <div className="grid lg:grid-cols-[auto,1fr] gap-6">
          <Card className="card-pad flex flex-col items-center justify-center gap-3 lg:w-64">
            <ScoreDial score={scores.overall} size={144} label={scores.grade} />
            <p className={clsx('text-sm font-medium text-center', scoreColor(scores.overall))}>
              {scores.verdict}
            </p>
            <div className="flex flex-wrap justify-center gap-1.5 pt-1">
              {SEVERITY_ORDER.filter((s) => scores.severity_counts?.[s]).map((s) => (
                <SeverityChip key={s} severity={s} count={scores.severity_counts[s]} />
              ))}
            </div>
          </Card>

          <Card className="card-pad">
            <SectionTitle icon={<Gauge className="w-4 h-4" />} title="Dimension scores"
                          subtitle="Weighted by impact on real-world risk" />
            <div className="grid sm:grid-cols-2 gap-x-8 gap-y-4">
              {Object.entries(scores.dimensions).map(([dim, value]) => (
                <ScoreBar key={dim} label={dim} value={value} weight={scores.weights?.[dim]} />
              ))}
            </div>
          </Card>
        </div>
      )}

      {/* tabs */}
      {(scores || review.files.length > 0 || !running) && (
        <div className="flex gap-1 p-1 bg-ink-850 rounded-lg w-fit">
          <PanelTab active={panel === 'overview'} onClick={() => setPanel('overview')}
                    icon={<Sparkles className="w-3.5 h-3.5" />} label="Overview" />
          <PanelTab active={panel === 'findings'} onClick={() => setPanel('findings')}
                    icon={<Target className="w-3.5 h-3.5" />} label="Findings"
                    badge={allFindings.length} />
          <PanelTab active={panel === 'files'} onClick={() => setPanel('files')}
                    icon={<FileCode2 className="w-3.5 h-3.5" />} label="Files"
                    badge={review.files.length} />
          <PanelTab active={panel === 'trace'} onClick={() => setPanel('trace')}
                    icon={<Layers className="w-3.5 h-3.5" />} label="Agent trace" />
        </div>
      )}

      {/* overview */}
      {panel === 'overview' && (
        <div className="space-y-6">
          {profile && (
            <Card className="card-pad">
              <SectionTitle icon={<Boxes className="w-4 h-4" />} title="What this project is"
                            subtitle={profile.one_liner} />
              <p className="text-sm text-ink-300 leading-relaxed">
                {narrative?.what_the_project_does || profile.purpose}
              </p>
              <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-4 mt-5 pt-5 border-t border-ink-850">
                <Fact label="Type" value={profile.project_type} />
                <Fact label="Domain" value={profile.domain} />
                <Fact label="Language" value={profile.primary_language} />
                <Fact label="Maturity" value={profile.maturity} />
              </div>
              {!!profile.frameworks?.length && (
                <div className="flex flex-wrap gap-1.5 mt-4">
                  {[...profile.frameworks, ...profile.key_technologies].slice(0, 14).map((t) => (
                    <Tag key={t}>{t}</Tag>
                  ))}
                </div>
              )}
            </Card>
          )}

          {review.summary && (
            <Card className="card-pad">
              <SectionTitle icon={<Sparkles className="w-4 h-4" />} title="Executive summary" />
              <p className="text-sm text-ink-300 leading-relaxed whitespace-pre-line">
                {review.summary}
              </p>
              {narrative?.overall_verdict && (
                <p className="text-sm text-ink-200 leading-relaxed mt-4 pt-4 border-t border-ink-850">
                  {narrative.overall_verdict}
                </p>
              )}
            </Card>
          )}

          {(narrative?.top_strengths?.length || narrative?.top_risks?.length) && (
            <div className="grid md:grid-cols-2 gap-6">
              {!!narrative?.top_strengths?.length && (
                <Card className="card-pad">
                  <SectionTitle title="Strengths" />
                  <ul className="space-y-2">
                    {narrative.top_strengths.map((s, i) => (
                      <li key={i} className="flex gap-2.5 text-sm text-ink-300">
                        <span className="text-good shrink-0 mt-0.5">+</span>{s}
                      </li>
                    ))}
                  </ul>
                </Card>
              )}
              {!!narrative?.top_risks?.length && (
                <Card className="card-pad">
                  <SectionTitle title="Key risks" />
                  <ul className="space-y-2">
                    {narrative.top_risks.map((r, i) => (
                      <li key={i} className="flex gap-2.5 text-sm text-ink-300">
                        <span className="text-sev-critical shrink-0 mt-0.5">!</span>{r}
                      </li>
                    ))}
                  </ul>
                </Card>
              )}
            </div>
          )}

          {review.structure && (
            <Card className="card-pad">
              <SectionTitle icon={<Network className="w-4 h-4" />} title="Structure analysis"
                            subtitle={review.structure.organization_pattern} />
              {review.structure.coupling_assessment && (
                <p className="text-sm text-ink-300 leading-relaxed mb-4">
                  {review.structure.coupling_assessment}
                </p>
              )}
              <div className="grid md:grid-cols-3 gap-5">
                <ListBlock title="Strengths" items={review.structure.strengths} tone="text-good" />
                <ListBlock title="Concerns" items={review.structure.concerns} tone="text-sev-medium" />
                <ListBlock title="Missing" items={review.structure.missing_pieces} tone="text-sev-high" />
              </div>
              {!!review.structure.modules?.length && (
                <div className="mt-5 pt-5 border-t border-ink-850">
                  <div className="label">Modules</div>
                  <div className="grid sm:grid-cols-2 gap-3">
                    {review.structure.modules.slice(0, 8).map((m) => (
                      <div key={m.path} className="rounded-lg bg-ink-850 border border-ink-800 p-3">
                        <div className="text-sm font-medium text-ink-100">{m.name}</div>
                        <div className="mono text-[11px] text-ink-600 mt-0.5">{m.path}</div>
                        <p className="text-xs text-ink-400 mt-1.5">{m.responsibility}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </Card>
          )}

          <Recommendations review={review} />
          {review.metrics && <RunMetrics review={review} />}
        </div>
      )}

      {/* findings */}
      {panel === 'findings' && (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2">
            <FilterPill active={severityFilter === 'all'} onClick={() => setSeverityFilter('all')}
                        label={`All (${allFindings.length})`} />
            {SEVERITY_ORDER.map((sev) => {
              const n = allFindings.filter((f) => f.severity === sev).length
              if (!n) return null
              return (
                <FilterPill key={sev} active={severityFilter === sev}
                            onClick={() => setSeverityFilter(sev)} label={`${sev} (${n})`} />
              )
            })}
          </div>

          {proven.length > 0 && severityFilter === 'all' && (
            <Card className="card-pad border-sev-critical/30">
              <SectionTitle
                icon={<BadgeCheck className="w-4 h-4" />}
                title={`Demonstrated against your code (${proven.length})`}
                subtitle="Each of these was reproduced, not just suspected"
              />
              <div className="space-y-2">
                {proven.map((f) => <FindingCard key={f.id} finding={f} defaultOpen />)}
              </div>
            </Card>
          )}

          {review.cross_cutting_findings.length > 0 && severityFilter === 'all' && (
            <Card className="card-pad">
              <SectionTitle icon={<Network className="w-4 h-4" />} title="Systemic findings"
                            subtitle="Issues that span multiple files" />
              <div className="space-y-2">
                {review.cross_cutting_findings.map((f) => (
                  <FindingCard key={f.id} finding={f} />
                ))}
              </div>
            </Card>
          )}

          {filtered.length ? (
            <div className="space-y-2">
              {filtered
                .filter((f) => severityFilter !== 'all'
                  || (!f.is_cross_cutting && f.proof_status !== 'proven'))
                .map((f) => <FindingCard key={f.id} finding={f} />)}
            </div>
          ) : (
            <EmptyState title={running ? 'Findings appear as files are reviewed' : 'No findings'}
                        description={running ? undefined : 'Nothing matched this filter.'} />
          )}
        </div>
      )}

      {/* files */}
      {panel === 'files' && (
        review.files.length ? (
          <div className="space-y-2">
            {review.files.map((f) => <FileReportCard key={f.id} file={f} />)}
          </div>
        ) : (
          <EmptyState icon={<FileCode2 className="w-10 h-10" />} title="No file reports yet"
                      description={running ? 'The reviewer agent is still working.' : undefined} />
        )
      )}

      {/* trace */}
      {panel === 'trace' && (
        <Card className="card-pad">
          <SectionTitle icon={<Layers className="w-4 h-4" />} title="Agent trace"
                        subtitle={`${events.length} events across the pipeline`} />
          <AgentTrace events={events} live={connected && running} />
        </Card>
      )}
    </div>
  )
}

function Recommendations({ review }) {
  const recs = review.recommendations ?? []
  if (!recs.length) return null

  const groups = [
    {
      title: 'Quick wins', subtitle: 'High value, low effort',
      items: recs.filter((r) => r.horizon === 'quick_win'),
      icon: <Lightbulb className="w-4 h-4" />,
    },
    {
      title: 'Strategic work', subtitle: 'Worth planning for',
      items: recs.filter((r) => r.horizon === 'strategic'),
      icon: <Target className="w-4 h-4" />,
    },
  ]

  return (
    <div className="grid md:grid-cols-2 gap-6">
      {groups.map(({ title, subtitle, items, icon }) => items.length > 0 && (
        <Card key={title} className="card-pad">
          <SectionTitle icon={icon} title={title} subtitle={subtitle} />
          <div className="space-y-3">
            {items.map((r, i) => (
              <div key={i} className="rounded-lg bg-ink-850 border border-ink-800 p-3.5">
                <div className="flex items-start justify-between gap-3">
                  <h4 className="text-sm font-medium text-ink-100">{r.title}</h4>
                  <div className="flex gap-1.5 shrink-0">
                    <span className="chip border-ink-700 bg-ink-800 text-ink-400">{r.effort}</span>
                    <span className={clsx('chip',
                      r.impact === 'critical' || r.impact === 'high'
                        ? 'border-sev-high/30 bg-sev-high/10 text-sev-high'
                        : 'border-ink-700 bg-ink-800 text-ink-400')}>
                      {r.impact} impact
                    </span>
                  </div>
                </div>
                <p className="text-sm text-ink-400 mt-2 leading-relaxed">{r.detail}</p>
                {!!r.related_paths?.length && (
                  <div className="flex flex-wrap gap-1.5 mt-2.5">
                    {r.related_paths.slice(0, 5).map((p) => (
                      <span key={p}
                            className="mono text-[10px] text-ink-500 bg-ink-900 px-1.5 py-0.5 rounded">
                        {p}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </Card>
      ))}
    </div>
  )
}

function RunMetrics({ review }) {
  const m = review.metrics
  const usage = review.token_usage
  return (
    <Card className="card-pad">
      <SectionTitle title="Run metrics" />
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">
        <Fact label="Files indexed" value={String(m.files_indexed)} />
        <Fact label="Files reviewed" value={String(m.files_reviewed)} />
        <Fact label="Lines of code" value={compact(m.total_loc)} />
        <Fact label="Test ratio" value={`${Math.round((m.test_ratio ?? 0) * 100)}%`} />
        <Fact label="Mean file score"
              value={m.mean_file_score != null ? String(m.mean_file_score) : '—'} />
        <Fact label="Tokens used" value={usage ? compact(usage.total_tokens) : '—'} />
        <Fact label="Proven findings" value={String(m.proven_findings ?? 0)} />
      </div>
      {usage && (usage.cache_hits > 0 || usage.by_key) && (
        <div className="mt-4 pt-4 border-t border-ink-850 space-y-1.5 text-xs text-ink-600">
          <p>{usage.calls} LLM calls, {usage.cache_hits} served from cache.</p>
          {usage.by_key && Object.keys(usage.by_key).length > 0 && (
            <p>
              Spread across keys:{' '}
              {Object.entries(usage.by_key)
                .map(([k, v]) => `${k} ${compact(v)}`)
                .join(' · ')}
            </p>
          )}
          {!!usage.key_rotations && (
            <p className="text-sev-medium">
              {usage.key_rotations} key rotation(s) after rate limits.
            </p>
          )}
        </div>
      )}
      {m.inventory_truncated && (
        <p className="text-xs text-sev-medium mt-2">
          The file inventory hit its cap — some files were never indexed.
        </p>
      )}
    </Card>
  )
}

/**
 * Failure detail.
 *
 * `review.error` carries the actionable summary, then a blank line, then the
 * provider's own words. Both matter: the summary says what to change, the
 * detail proves why — so neither is hidden behind a click by default.
 */
function FailureBanner({ error, events }) {
  const [showDetail, setShowDetail] = useState(true)

  const [summary, ...rest] = String(error).split(/\n\n+/)
  const detail = rest.join('\n\n').trim()

  const failEvent = [...events].reverse().find((e) => e.type === 'review_failed')
  const code = failEvent?.payload?.code

  return (
    <div className="rounded-xl border border-sev-critical/30 bg-sev-critical/10 p-4">
      <div className="flex items-start gap-3">
        <AlertCircle className="w-5 h-5 text-sev-critical shrink-0 mt-0.5" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="font-medium text-sev-critical">Review failed</p>
            {code && (
              <span className="chip border-sev-critical/30 bg-sev-critical/10 text-sev-critical mono">
                {code}
              </span>
            )}
          </div>
          <p className="text-sm text-ink-200 mt-1.5 leading-relaxed whitespace-pre-line">
            {summary}
          </p>

          {detail && (
            <>
              <button
                onClick={() => setShowDetail((v) => !v)}
                className="text-xs text-ink-400 hover:text-ink-200 mt-2.5 inline-flex items-center gap-1"
              >
                <ChevronDown className={clsx('w-3 h-3 transition-transform',
                  !showDetail && '-rotate-90')} />
                {showDetail ? 'Hide' : 'Show'} technical detail
              </button>
              {showDetail && (
                <pre className="mono text-[11px] text-ink-400 bg-ink-950 border border-ink-800 rounded-lg p-3 mt-2 overflow-x-auto whitespace-pre-wrap">
                  {detail}
                </pre>
              )}
            </>
          )}

          <p className="text-xs text-ink-500 mt-3">
            Provider or key problem? Run{' '}
            <code className="mono text-ink-400 bg-ink-900 px-1 py-0.5 rounded">
              python -m app.llm.diagnose
            </code>{' '}
            in <code className="mono text-ink-400">backend/</code> to test every key and model.
          </p>
        </div>
      </div>
    </div>
  )
}

function Fact({ label, value }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-ink-500">{label}</div>
      <div className="text-sm text-ink-100 mt-1 capitalize truncate">{value || '—'}</div>
    </div>
  )
}

function ListBlock({ title, items, tone }) {
  if (!items?.length) return null
  return (
    <div>
      <div className="label">{title}</div>
      <ul className="space-y-1.5">
        {items.slice(0, 6).map((item, i) => (
          <li key={i} className="text-sm text-ink-300 flex gap-2">
            <span className={clsx('shrink-0', tone)}>•</span>{item}
          </li>
        ))}
      </ul>
    </div>
  )
}

function StatusChip({ status }) {
  const tone = {
    completed: 'border-good/30 bg-good/10 text-good',
    running: 'border-accent/30 bg-accent/10 text-accent-glow',
    queued: 'border-ink-700 bg-ink-850 text-ink-400',
    failed: 'border-sev-critical/30 bg-sev-critical/10 text-sev-critical',
    cancelled: 'border-sev-high/30 bg-sev-high/10 text-sev-high',
  }
  return <span className={clsx('chip', tone[status] ?? tone.queued)}>{status}</span>
}

function PanelTab({ active, onClick, icon, label, badge }) {
  return (
    <button onClick={onClick} className={clsx(
      'flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm transition-colors',
      active ? 'bg-ink-750 text-ink-100 font-medium' : 'text-ink-400 hover:text-ink-200',
    )}>
      {icon}{label}
      {badge !== undefined && badge > 0 && (
        <span className="text-[10px] tabular-nums bg-ink-800 text-ink-400 rounded px-1.5 py-0.5">
          {badge}
        </span>
      )}
    </button>
  )
}

function FilterPill({ active, onClick, label }) {
  return (
    <button onClick={onClick} className={clsx('chip capitalize transition-colors',
      active ? 'border-accent/40 bg-accent/15 text-accent-glow'
             : 'border-ink-700 bg-ink-850 text-ink-400 hover:text-ink-200')}>
      {label}
    </button>
  )
}
