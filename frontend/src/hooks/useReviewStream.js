import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '@/api/client'

const TERMINAL = new Set(['completed', 'failed', 'cancelled'])

// Named SSE events don't fire `onmessage`, so every type we emit is bound.
const NAMED_EVENTS = [
  'review_started', 'source_ready', 'phase', 'progress', 'inventory_ready',
  'agent_start', 'agent_done', 'profile_ready', 'structure_ready',
  'plan_ready', 'review_plan_ready', 'files_reviewed', 'cross_cutting_ready',
  'critique_complete', 'budget_exhausted', 'key_rotated', 'review_completed',
  'review_failed', 'review_cancelled',
]

/**
 * Live agent trace for one review.
 *
 * Events are keyed by `seq` so the SSE replay-on-reconnect can overlap with
 * what we already have without duplicating rows. The full review is refetched
 * when the run reaches a terminal state, which is when the scores, findings
 * and report actually exist.
 */
export function useReviewStream(reviewId) {
  const [review, setReview] = useState(null)
  const [events, setEvents] = useState([])
  const [connected, setConnected] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const seenSeq = useRef(new Set())
  const sourceRef = useRef(null)

  const refresh = useCallback(async () => {
    if (!reviewId) return
    try {
      setReview(await api.getReview(reviewId))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load review')
    }
  }, [reviewId])

  const pushEvent = useCallback((event) => {
    if (seenSeq.current.has(event.seq)) return
    seenSeq.current.add(event.seq)
    setEvents((prev) => [...prev, event].sort((a, b) => a.seq - b.seq))
  }, [])

  useEffect(() => {
    if (!reviewId) return

    let cancelled = false
    seenSeq.current = new Set()
    setEvents([])
    setLoading(true)

    ;(async () => {
      let detail = null
      try {
        detail = await api.getReview(reviewId)
        if (cancelled) return
        setReview(detail)
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : 'Review not found')
          setLoading(false)
        }
        return
      }

      // Finished runs need the persisted trace, not a live socket.
      if (TERMINAL.has(detail.status)) {
        try {
          const past = await api.getEvents(reviewId)
          if (!cancelled) past.forEach(pushEvent)
        } catch {
          /* the trace is a nicety; the report is already loaded */
        }
        if (!cancelled) setLoading(false)
        return
      }

      if (cancelled) return
      setLoading(false)

      const source = new EventSource(api.streamUrl(reviewId))
      sourceRef.current = source

      source.onopen = () => setConnected(true)

      source.onerror = () => {
        setConnected(false)
        // EventSource retries on its own; only give up once the server has
        // closed the stream for good.
        if (source.readyState === EventSource.CLOSED) {
          source.close()
          void refresh()
        }
      }

      const onAny = (e) => {
        try {
          const data = JSON.parse(e.data)
          if (typeof data.seq === 'number') pushEvent(data)

          if (data.type === 'phase' || data.type === 'progress') {
            setReview((prev) =>
              prev
                ? {
                    ...prev,
                    phase: data.payload?.phase ?? prev.phase,
                    progress: data.payload?.progress ?? prev.progress,
                    status: 'running',
                  }
                : prev,
            )
          }
        } catch {
          /* keep-alive frames are not JSON payloads we care about */
        }
      }

      NAMED_EVENTS.forEach((name) => source.addEventListener(name, onAny))
      source.onmessage = onAny

      source.addEventListener('done', () => {
        source.close()
        setConnected(false)
        void refresh()
      })
    })()

    return () => {
      cancelled = true
      sourceRef.current?.close()
      sourceRef.current = null
      setConnected(false)
    }
  }, [reviewId, pushEvent, refresh])

  return { review, events, connected, loading, error, refresh }
}
