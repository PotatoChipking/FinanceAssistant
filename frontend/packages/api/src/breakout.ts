import { fetchAPI } from './client'

export interface BreakoutStrength {
  strong: boolean
  industry: string
  industry_as_of: string
  price_as_of: string
  industry_rank: number
  industry_count: number
  stock_rank: number
  peer_count: number
  industry_return_pct: number
  stock_return_pct: number
}

export interface BreakoutWatchItem {
  id?: number
  symbol: string
  name: string | null
  breakout_date: string | null
  breakout_level: number | null
  reason: string | null
  strength: BreakoutStrength | null
}

export interface BreakoutWatchlist {
  snapshot_date: string | null
  items: BreakoutWatchItem[]
}

export interface BreakoutPreviewResult extends BreakoutWatchlist {
  status: 'ok' | 'partial' | 'data_unavailable'
  scanned: number
  evaluated: number
}

export interface BreakoutPreviewTask {
  id: string
  status: 'queued' | 'running' | 'success' | 'failed' | 'stale'
  progress_current: number
  progress_total: number
  result: Partial<BreakoutPreviewResult>
  error: string
}

export const breakoutApi = {
  listBWatchlist: () => fetchAPI<BreakoutWatchlist>('/breakout/b-watchlist'),
  startPreview: () => fetchAPI<BreakoutPreviewTask>('/breakout/b-scan', { method: 'POST' }),
  latestPreview: () => fetchAPI<{ items: BreakoutPreviewTask[] }>('/tasks?type=breakout_b_preview&limit=1'),
  getPreview: (id: string) => fetchAPI<BreakoutPreviewTask>(`/tasks/${encodeURIComponent(id)}`),
}
