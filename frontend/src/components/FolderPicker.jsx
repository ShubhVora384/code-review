import clsx from 'clsx'
import {
  AlertCircle, ChevronRight, CornerLeftUp, Eye, EyeOff, Folder,
  FolderGit2, HardDrive, Loader2, PackageOpen, X,
} from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { api } from '@/api/client'

/**
 * Modal directory browser.
 *
 * The browser cannot hand us a real filesystem path, so the server walks its
 * own disk and we render the result. Only directories inside the API's
 * configured roots are reachable.
 */
export default function FolderPicker({ open, initialPath, onSelect, onClose }) {
  const [path, setPath] = useState(initialPath || '')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [showHidden, setShowHidden] = useState(false)

  const load = useCallback(async (target) => {
    setLoading(true)
    setError(null)
    try {
      const result = await api.browse(target, showHidden)
      setData(result)
      setPath(result.path ?? '')
    } catch (e) {
      setError(e?.message || 'Could not read that folder')
    } finally {
      setLoading(false)
    }
  }, [showHidden])

  useEffect(() => {
    if (open) load(initialPath || '')
  }, [open, initialPath, load])

  useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const entries = data?.entries ?? []
  const atRoots = data?.path == null
  const crumbs = buildCrumbs(data)

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-ink-950/80 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="card w-full max-w-2xl max-h-[80vh] flex flex-col shadow-2xl"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Choose a folder"
      >
        {/* header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-ink-800">
          <Folder className="w-4 h-4 text-accent shrink-0" />
          <h2 className="text-sm font-semibold flex-1">Choose a folder</h2>
          <button
            onClick={() => setShowHidden((v) => !v)}
            title={showHidden ? 'Hide hidden folders' : 'Show hidden folders'}
            className="p-1.5 rounded-md text-ink-500 hover:text-ink-200 hover:bg-ink-800"
          >
            {showHidden ? <Eye className="w-4 h-4" /> : <EyeOff className="w-4 h-4" />}
          </button>
          <button
            onClick={onClose}
            aria-label="Close"
            className="p-1.5 rounded-md text-ink-500 hover:text-ink-200 hover:bg-ink-800"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* breadcrumb */}
        <div className="flex items-center gap-1 px-5 py-2.5 border-b border-ink-850 overflow-x-auto">
          <button
            onClick={() => load('')}
            className="shrink-0 p-1 rounded text-ink-500 hover:text-accent"
            title="Back to roots"
          >
            <HardDrive className="w-3.5 h-3.5" />
          </button>
          {crumbs.map((c) => (
            <span key={c.path} className="flex items-center gap-1 shrink-0">
              <ChevronRight className="w-3 h-3 text-ink-700" />
              <button
                onClick={() => load(c.path)}
                className="mono text-[11px] text-ink-400 hover:text-accent px-1 py-0.5 rounded"
              >
                {c.name}
              </button>
            </span>
          ))}
        </div>

        {/* listing */}
        <div className="flex-1 overflow-y-auto min-h-[16rem]">
          {loading && (
            <div className="flex items-center justify-center gap-2 py-16 text-ink-400 text-sm">
              <Loader2 className="w-4 h-4 animate-spin" /> Reading folder…
            </div>
          )}

          {!loading && error && (
            <div className="flex items-start gap-2.5 m-5 p-3.5 rounded-lg border border-sev-critical/30 bg-sev-critical/10">
              <AlertCircle className="w-4 h-4 text-sev-critical shrink-0 mt-0.5" />
              <div className="text-sm">
                <p className="text-sev-critical">{error}</p>
                <button onClick={() => load('')} className="text-ink-400 hover:text-ink-200 mt-1.5 text-xs">
                  ← Back to the start
                </button>
              </div>
            </div>
          )}

          {!loading && !error && (
            <div className="p-2">
              {data?.parent && (
                <button
                  onClick={() => load(data.parent)}
                  className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-left hover:bg-ink-850 text-ink-400"
                >
                  <CornerLeftUp className="w-4 h-4 shrink-0" />
                  <span className="text-sm">Up one level</span>
                </button>
              )}

              {entries.length === 0 ? (
                <p className="text-sm text-ink-500 text-center py-12">
                  No sub-folders here.
                  {!atRoots && ' You can still select this folder below.'}
                </p>
              ) : (
                entries.map((entry) => (
                  <button
                    key={entry.path}
                    onClick={() => load(entry.path)}
                    onDoubleClick={() => onSelect(entry.path)}
                    disabled={!entry.readable}
                    className={clsx(
                      'w-full flex items-center gap-3 px-3 py-2 rounded-lg text-left transition-colors',
                      entry.readable ? 'hover:bg-ink-850' : 'opacity-40 cursor-not-allowed',
                    )}
                  >
                    {entry.is_git_repo
                      ? <FolderGit2 className="w-4 h-4 shrink-0 text-accent" />
                      : entry.is_project
                        ? <PackageOpen className="w-4 h-4 shrink-0 text-good" />
                        : <Folder className="w-4 h-4 shrink-0 text-ink-500" />}

                    <span className="min-w-0 flex-1">
                      <span className="block text-sm text-ink-100 truncate">{entry.name}</span>
                      {!entry.readable && (
                        <span className="block text-[11px] text-ink-600">permission denied</span>
                      )}
                    </span>

                    {entry.is_project && (
                      <span className="chip border-accent/25 bg-accent/10 text-accent-glow shrink-0">
                        {entry.is_git_repo ? 'git repo' : 'project'}
                      </span>
                    )}
                    {entry.child_dirs > 0 && (
                      <ChevronRight className="w-4 h-4 shrink-0 text-ink-600" />
                    )}
                  </button>
                ))
              )}
            </div>
          )}
        </div>

        {/* footer */}
        <div className="flex items-center gap-3 px-5 py-4 border-t border-ink-800">
          <div className="min-w-0 flex-1">
            <div className="text-[10px] uppercase tracking-wider text-ink-500">Selected</div>
            <div className="mono text-[11px] text-ink-300 truncate">
              {path || 'nothing yet — open a folder'}
            </div>
          </div>
          <button onClick={onClose} className="btn-ghost">Cancel</button>
          <button
            onClick={() => onSelect(path)}
            disabled={!path}
            className="btn-primary"
          >
            Use this folder
          </button>
        </div>
      </div>
    </div>
  )
}

function buildCrumbs(data) {
  if (!data?.path) return []
  const root = (data.roots || []).find((r) => data.path.startsWith(r))
  if (!root) return [{ name: data.path, path: data.path }]

  const crumbs = [{ name: root, path: root }]
  const rest = data.path.slice(root.length).split('/').filter(Boolean)
  let acc = root
  for (const part of rest) {
    acc = `${acc}/${part}`
    crumbs.push({ name: part, path: acc })
  }
  return crumbs
}
