export const SEVERITY_META = {
  critical: { label: 'Critical', color: 'text-sev-critical', bg: 'bg-sev-critical/10', border: 'border-sev-critical/30' },
  high:     { label: 'High',     color: 'text-sev-high',     bg: 'bg-sev-high/10',     border: 'border-sev-high/30' },
  medium:   { label: 'Medium',   color: 'text-sev-medium',   bg: 'bg-sev-medium/10',   border: 'border-sev-medium/30' },
  low:      { label: 'Low',      color: 'text-sev-low',      bg: 'bg-sev-low/10',      border: 'border-sev-low/30' },
  info:     { label: 'Info',     color: 'text-sev-info',     bg: 'bg-sev-info/10',     border: 'border-sev-info/30' },
}

export const SEVERITY_ORDER = ['critical', 'high', 'medium', 'low', 'info']

export function scoreColor(score) {
  if (score == null) return 'text-ink-400'
  if (score >= 90) return 'text-good'
  if (score >= 80) return 'text-sev-low'
  if (score >= 70) return 'text-sev-medium'
  if (score >= 60) return 'text-sev-high'
  return 'text-sev-critical'
}

export function scoreStroke(score) {
  if (score == null) return '#4a5265'
  if (score >= 90) return '#3fb984'
  if (score >= 80) return '#4a9ee8'
  if (score >= 70) return '#e3bd3a'
  if (score >= 60) return '#f08b3c'
  return '#f0526a'
}

const PHASE_LABELS = {
  queued: 'Queued',
  ingesting: 'Fetching the codebase',
  profiling: 'Identifying the project',
  structure: 'Analysing structure',
  planning: 'Planning the review',
  reviewing_files: 'Reviewing files',
  cross_cutting: 'Finding systemic issues',
  critique: 'Challenging findings',
  verifying: 'Proving findings',
  synthesizing: 'Writing the report',
  completed: 'Complete',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

export const phaseLabel = (p) => PHASE_LABELS[p] ?? p

// Must stay in step with PHASE_PROGRESS in backend/app/agents/context.py
export const PHASE_SEQUENCE = [
  'ingesting', 'profiling', 'structure', 'planning',
  'reviewing_files', 'cross_cutting', 'critique', 'verifying', 'synthesizing',
]

export function relativeTime(iso) {
  const secs = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (secs < 60) return 'just now'
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`
  return `${Math.floor(secs / 86400)}d ago`
}

export function duration(from, to) {
  if (!from || !to) return null
  const ms = new Date(to).getTime() - new Date(from).getTime()
  if (ms < 0) return null
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m ${s % 60}s`
}

export const compact = (n) =>
  n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M`
  : n >= 1_000 ? `${(n / 1_000).toFixed(1)}k`
  : String(n)

export const fileName = (p) => p.split('/').pop() ?? p

export const dirName = (p) => {
  const parts = p.split('/')
  parts.pop()
  return parts.join('/')
}

export const PROOF_META = {
  proven: {
    label: 'Proven',
    hint: 'Reproduced against your code',
    color: 'text-sev-critical',
    bg: 'bg-sev-critical/10',
    border: 'border-sev-critical/40',
  },
  inconclusive: {
    label: 'Unconfirmed',
    hint: 'Could not be demonstrated automatically',
    color: 'text-ink-400',
    bg: 'bg-ink-850',
    border: 'border-ink-700',
  },
  not_attempted: {
    label: 'Not tested',
    hint: 'No automated check for this kind of issue yet',
    color: 'text-ink-500',
    bg: 'bg-ink-850',
    border: 'border-ink-800',
  },
}

export const proofMeta = (status) => PROOF_META[status] ?? PROOF_META.not_attempted

export const LAUNCH_META = {
  do_not_launch: { icon: '🛑', tone: 'border-sev-critical/40 bg-sev-critical/10', color: 'text-sev-critical' },
  fix_first:     { icon: '⚠️', tone: 'border-sev-high/40 bg-sev-high/10',       color: 'text-sev-high' },
  launch_with_care: { icon: '✅', tone: 'border-good/30 bg-good/10',            color: 'text-good' },
  ready:         { icon: '✅', tone: 'border-good/30 bg-good/10',              color: 'text-good' },
}
