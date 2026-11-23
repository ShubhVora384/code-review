import clsx from 'clsx'
import { Activity, History, KeyRound, ScanSearch } from 'lucide-react'
import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'

import { api } from './api/client'

export default function App() {
  const [health, setHealth] = useState(null)

  useEffect(() => {
    const load = () => api.health().then(setHealth).catch(() => setHealth(null))
    load()
    const timer = setInterval(load, 30_000)
    return () => clearInterval(timer)
  }, [])

  const deep = health?.budget?.deep
  const usedPct = deep ? deep.day_pct : 0
  const keys = health?.keys

  return (
    <div className="min-h-full flex flex-col">
      <header className="sticky top-0 z-30 border-b border-ink-850 bg-ink-950/85 backdrop-blur-xl">
        <div className="mx-auto max-w-7xl px-5 h-14 flex items-center gap-6">
          <NavLink to="/" className="flex items-center gap-2.5 shrink-0">
            <div className="w-7 h-7 rounded-lg bg-accent grid place-items-center">
              <ScanSearch className="w-4 h-4 text-ink-950" strokeWidth={2.5} />
            </div>
            <span className="font-semibold tracking-tight">Code Review</span>
          </NavLink>

          <nav className="flex items-center gap-1">
            <Tab to="/" end icon={<ScanSearch className="w-3.5 h-3.5" />}>New review</Tab>
            <Tab to="/reviews" icon={<History className="w-3.5 h-3.5" />}>History</Tab>
          </nav>

          <div className="ml-auto flex items-center gap-4 text-xs">
            {health && !health.groq_configured && (
              <span className="chip border-sev-high/30 bg-sev-high/10 text-sev-high">
                No Groq API key set
              </span>
            )}

            {keys?.total > 0 && <KeyPool keys={keys} />}

            {deep && (
              <div
                className="hidden lg:flex items-center gap-2"
                title={
                  `Deep tier today: ${deep.tokens_today.toLocaleString()} / ` +
                  `${deep.tokens_per_day.toLocaleString()} tokens across all keys`
                }
              >
                <span className="text-ink-500">Budget</span>
                <div className="w-16 h-1.5 rounded-full bg-ink-800 overflow-hidden">
                  <div
                    className={clsx('h-full rounded-full transition-all',
                      usedPct > 85 ? 'bg-sev-critical'
                        : usedPct > 60 ? 'bg-sev-medium' : 'bg-good')}
                    style={{ width: `${Math.max(2, usedPct)}%` }}
                  />
                </div>
                <span className="tabular-nums text-ink-400">{usedPct.toFixed(0)}%</span>
              </div>
            )}

            <span className="flex items-center gap-1.5 text-ink-500">
              <Activity className={clsx('w-3.5 h-3.5',
                health?.status === 'ok' ? 'text-good' : 'text-sev-high')} />
              <span className="hidden xl:inline">{health?.models?.deep ?? 'offline'}</span>
            </span>
          </div>
        </div>
      </header>

      <main className="flex-1 mx-auto w-full max-w-7xl px-5 py-8">
        <Outlet />
      </main>

      <footer className="border-t border-ink-850 py-5">
        <div className="mx-auto max-w-7xl px-5 text-xs text-ink-600 flex flex-wrap gap-x-4 gap-y-1">
          <span>Agentic review pipeline on LangGraph</span>
          <span>·</span>
          <span>Groq {health?.models?.deep}</span>
          <span>·</span>
          <span>{health?.database}</span>
        </div>
      </footer>
    </div>
  )
}

/** One dot per configured API key: green available, amber cooling, red spent. */
function KeyPool({ keys }) {
  const title = keys.keys
    .map((k) => `${k.id}: ${k.state}` + (k.cooldown_remaining_s
      ? ` (${k.cooldown_remaining_s}s)` : ''))
    .join('\n')

  return (
    <div className="hidden md:flex items-center gap-1.5" title={title}>
      <KeyRound className="w-3.5 h-3.5 text-ink-500" />
      <div className="flex gap-1">
        {keys.keys.map((k) => (
          <span
            key={k.id}
            className={clsx('w-1.5 h-1.5 rounded-full',
              k.state === 'available' ? 'bg-good'
                : k.state === 'cooling' ? 'bg-sev-medium animate-pulse'
                : 'bg-sev-critical')}
          />
        ))}
      </div>
      <span className="text-ink-500 tabular-nums">{keys.available}/{keys.total}</span>
    </div>
  )
}

function Tab({ to, end, icon, children }) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) => clsx(
        'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition-colors',
        isActive ? 'bg-ink-850 text-ink-100' : 'text-ink-400 hover:text-ink-200',
      )}
    >
      {icon}{children}
    </NavLink>
  )
}
