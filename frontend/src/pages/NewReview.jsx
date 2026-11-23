import clsx from 'clsx'
import { AlertCircle, FolderOpen, FolderSearch, Github, Loader2, Upload } from 'lucide-react'
import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { ApiError, api } from '@/api/client'
import FolderPicker from '@/components/FolderPicker'
import { Card, SectionTitle } from '@/components/Primitives'

const DEFAULT_OPTIONS = {
  max_files_to_review: 25,
  include_tests: true,
  deep_security_pass: true,
  enable_critic: true,
  focus_areas: [],
}

const FOCUS_CHOICES = [
  'security', 'performance', 'correctness',
  'maintainability', 'testing', 'architecture',
]

export default function NewReview() {
  const navigate = useNavigate()
  const fileInput = useRef(null)

  const [tab, setTab] = useState('git')
  const [gitUrl, setGitUrl] = useState('')
  const [branch, setBranch] = useState('')
  const [token, setToken] = useState('')
  const [localPath, setLocalPath] = useState('')
  const [zipFile, setZipFile] = useState(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [dragging, setDragging] = useState(false)

  const [options, setOptions] = useState(DEFAULT_OPTIONS)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  const canSubmit =
    tab === 'git' ? gitUrl.trim().length > 0
    : tab === 'local' ? localPath.trim().length > 0
    : zipFile !== null

  async function submit() {
    if (!canSubmit || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      const review =
        tab === 'zip'
          ? await api.uploadReview(zipFile, options)
          : await api.createReview({
              source_type: tab,
              source_ref: tab === 'git' ? gitUrl.trim() : localPath.trim(),
              branch: tab === 'git' && branch.trim() ? branch.trim() : null,
              access_token: tab === 'git' && token.trim() ? token.trim() : null,
              options,
            })
      navigate(`/reviews/${review.id}`)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not start the review')
      setSubmitting(false)
    }
  }

  function pickZip(file) {
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.zip')) {
      setError('Please choose a .zip archive')
      return
    }
    setError(null)
    setZipFile(file)
  }

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      <div className="text-center mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Review a codebase</h1>
        <p className="text-ink-400 mt-2 text-sm max-w-lg mx-auto">
          Seven agents work in sequence — they identify the project, map its structure,
          plan the review, read each file, hunt systemic issues, challenge their own
          findings, then score and report.
        </p>
      </div>

      <Card className="card-pad">
        <div className="flex gap-1 p-1 bg-ink-850 rounded-lg mb-5">
          <SourceTab active={tab === 'git'} onClick={() => setTab('git')}
                     icon={<Github className="w-4 h-4" />} label="Git repository" />
          <SourceTab active={tab === 'local'} onClick={() => setTab('local')}
                     icon={<FolderOpen className="w-4 h-4" />} label="Local folder" />
          <SourceTab active={tab === 'zip'} onClick={() => setTab('zip')}
                     icon={<Upload className="w-4 h-4" />} label="Upload ZIP" />
        </div>

        {tab === 'git' && (
          <div className="space-y-4">
            <div>
              <label className="label" htmlFor="giturl">Repository URL</label>
              <input id="giturl" className="input mono" placeholder="https://github.com/owner/repo"
                     value={gitUrl} onChange={(e) => setGitUrl(e.target.value)}
                     onKeyDown={(e) => e.key === 'Enter' && submit()} />
            </div>
            <div className="grid sm:grid-cols-2 gap-4">
              <div>
                <label className="label" htmlFor="branch">
                  Branch <span className="normal-case text-ink-600">(optional)</span>
                </label>
                <input id="branch" className="input mono" placeholder="main"
                       value={branch} onChange={(e) => setBranch(e.target.value)} />
              </div>
              <div>
                <label className="label" htmlFor="pat">
                  Access token <span className="normal-case text-ink-600">(private repos)</span>
                </label>
                <input id="pat" type="password" className="input mono" placeholder="ghp_…"
                       value={token} onChange={(e) => setToken(e.target.value)} />
              </div>
            </div>
            <p className="text-xs text-ink-600">
              The token is used for the clone and never written to the database.
            </p>
          </div>
        )}

        {tab === 'local' && (
          <div>
            <label className="label" htmlFor="path">Project folder</label>
            <div className="flex gap-2">
              <input
                id="path" className="input mono flex-1"
                placeholder="Click Browse, or type a path"
                value={localPath}
                onChange={(e) => setLocalPath(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && submit()}
              />
              <button type="button" onClick={() => setPickerOpen(true)} className="btn-ghost shrink-0">
                <FolderSearch className="w-4 h-4" /> Browse…
              </button>
            </div>
            <p className="text-xs text-ink-600 mt-2">
              Folders are read from the machine running the API, so the picker
              shows the server&apos;s disk rather than your browser&apos;s.
            </p>
          </div>
        )}

        {tab === 'zip' && (
          <div
            onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault(); setDragging(false)
              pickZip(e.dataTransfer.files?.[0])
            }}
            onClick={() => fileInput.current?.click()}
            className={clsx(
              'border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-colors',
              dragging ? 'border-accent bg-accent/5' : 'border-ink-700 hover:border-ink-600',
            )}
          >
            <input ref={fileInput} type="file" accept=".zip" className="hidden"
                   onChange={(e) => pickZip(e.target.files?.[0])} />
            <Upload className="w-7 h-7 mx-auto text-ink-500 mb-3" />
            {zipFile ? (
              <div>
                <p className="text-sm text-ink-100 font-medium">{zipFile.name}</p>
                <p className="text-xs text-ink-500 mt-1">
                  {(zipFile.size / 1024 / 1024).toFixed(1)} MB · click to replace
                </p>
              </div>
            ) : (
              <div>
                <p className="text-sm text-ink-200">Drop a .zip archive here</p>
                <p className="text-xs text-ink-500 mt-1">or click to browse</p>
              </div>
            )}
          </div>
        )}
      </Card>

      <Card className="card-pad">
        <SectionTitle title="Review settings"
                      subtitle="Fewer files finish faster on Groq's free tier." />

        <div className="space-y-5">
          <div>
            <div className="flex items-baseline justify-between mb-2">
              <label className="label mb-0" htmlFor="maxfiles">Files to review</label>
              <span className="text-sm font-semibold text-accent tabular-nums">
                {options.max_files_to_review}
              </span>
            </div>
            <input
              id="maxfiles" type="range" min={5} max={120} step={5}
              value={options.max_files_to_review}
              onChange={(e) => setOptions({ ...options, max_files_to_review: +e.target.value })}
              className="w-full accent-accent"
            />
            <div className="flex justify-between text-[10px] text-ink-600 mt-1">
              <span>5 · quick</span><span>120 · exhaustive</span>
            </div>
            <p className="text-xs text-ink-600 mt-2">
              Roughly{' '}
              <span className="text-ink-400">
                {Math.max(1, Math.round(options.max_files_to_review * 0.45))}–
                {Math.max(2, Math.round(options.max_files_to_review * 0.8))} minutes
              </span>{' '}
              on one free key, faster with several in rotation.
            </p>
          </div>

          <div>
            <span className="label">Focus areas</span>
            <div className="flex flex-wrap gap-2">
              {FOCUS_CHOICES.map((area) => {
                const on = options.focus_areas.includes(area)
                return (
                  <button
                    key={area}
                    onClick={() => setOptions({
                      ...options,
                      focus_areas: on
                        ? options.focus_areas.filter((a) => a !== area)
                        : [...options.focus_areas, area],
                    })}
                    className={clsx('chip capitalize transition-colors',
                      on ? 'border-accent/40 bg-accent/15 text-accent-glow'
                         : 'border-ink-700 bg-ink-850 text-ink-400 hover:text-ink-200')}
                  >
                    {area}
                  </button>
                )
              })}
            </div>
          </div>

          <div className="space-y-2.5">
            <Toggle label="Include test files"
                    hint="Review tests alongside source"
                    checked={options.include_tests}
                    onChange={(v) => setOptions({ ...options, include_tests: v })} />
            <Toggle label="Verify findings with a critic agent"
                    hint="Discards unsupported claims. Costs extra tokens, prevents false positives."
                    checked={options.enable_critic}
                    onChange={(v) => setOptions({ ...options, enable_critic: v })} />
          </div>
        </div>
      </Card>

      {error && (
        <div className="flex items-start gap-2.5 rounded-lg border border-sev-critical/30 bg-sev-critical/10 p-3.5">
          <AlertCircle className="w-4 h-4 text-sev-critical shrink-0 mt-0.5" />
          <p className="text-sm text-sev-critical">{error}</p>
        </div>
      )}

      <button onClick={submit} disabled={!canSubmit || submitting}
              className="btn-primary w-full py-3 text-base">
        {submitting
          ? <><Loader2 className="w-4 h-4 animate-spin" /> Starting…</>
          : 'Start review'}
      </button>

      <FolderPicker
        open={pickerOpen}
        initialPath={localPath}
        onClose={() => setPickerOpen(false)}
        onSelect={(chosen) => {
          setLocalPath(chosen)
          setPickerOpen(false)
          setError(null)
        }}
      />
    </div>
  )
}

function SourceTab({ active, onClick, icon, label }) {
  return (
    <button onClick={onClick} className={clsx(
      'flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-md text-sm transition-colors',
      active ? 'bg-ink-750 text-ink-100 font-medium' : 'text-ink-400 hover:text-ink-200',
    )}>
      {icon}<span className="hidden sm:inline">{label}</span>
    </button>
  )
}

function Toggle({ label, hint, checked, onChange }) {
  return (
    <label className="flex items-start gap-3 cursor-pointer group">
      <button
        type="button" role="switch" aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={clsx('shrink-0 mt-0.5 w-9 h-5 rounded-full transition-colors relative',
          checked ? 'bg-accent' : 'bg-ink-700')}
      >
        <span className={clsx(
          'absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform',
          checked ? 'translate-x-4' : 'translate-x-0.5')} />
      </button>
      <span className="min-w-0">
        <span className="block text-sm text-ink-200 group-hover:text-ink-100">{label}</span>
        {hint && <span className="block text-xs text-ink-600 mt-0.5">{hint}</span>}
      </span>
    </label>
  )
}
