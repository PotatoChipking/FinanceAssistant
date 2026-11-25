import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { expect, it, vi } from 'vitest'
import { breakoutApi, type BreakoutWatchItem } from '@panwatch/api'
import BreakoutWatchlistPage from './BreakoutWatchlist'

vi.mock('@panwatch/api', () => ({
  breakoutApi: { listBWatchlist: vi.fn(), latestPreview: vi.fn(), startPreview: vi.fn(), getPreview: vi.fn() },
}))
vi.mock('@panwatch/biz-ui/components/stock-insight-modal', () => ({
  default: ({ symbol }: { symbol: string }) => <div data-testid="insight">{symbol}</div>,
}))

it('默认保留强势、普通和未评估候选，并可以单独查看强势 B', async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  const container = document.body.appendChild(document.createElement('div'))
  const root = createRoot(container)
  vi.mocked(breakoutApi.latestPreview).mockResolvedValue({ items: [] })
  const item: BreakoutWatchItem = {
    id: 1, symbol: '600001', name: '强势样本', breakout_date: '2026-11-19', breakout_level: 10, reason: null,
    strength: {
      strong: true, industry: '银行', industry_as_of: '2026-11-24', price_as_of: '2026-11-18',
      industry_rank: 1, industry_count: 15, stock_rank: 2, peer_count: 20,
      industry_return_pct: 2, stock_return_pct: 5,
    },
  }
  vi.mocked(breakoutApi.listBWatchlist).mockResolvedValue({
    snapshot_date: '2026-11-24',
    items: [
      item,
      { ...item, id: 2, name: '普通样本', strength: { ...item.strength!, strong: false } },
      { ...item, id: 3, name: '缺数据样本', strength: null },
    ],
  })

  try {
    await act(async () => root.render(<BreakoutWatchlistPage />))
    expect(container.querySelectorAll('tbody tr')).toHaveLength(3)
    expect(container.textContent).toContain('强度未评估')
    const buttons = Array.from(container.querySelectorAll('button'))
    act(() => buttons.find(button => button.textContent === '强势 B 1')!.click())
    expect(container.querySelectorAll('tbody tr')).toHaveLength(1)
    act(() => buttons.find(button => button.textContent === '全部 B 类 3')!.click())
    expect(container.querySelectorAll('tbody tr')).toHaveLength(3)
    act(() => buttons.find(button => button.textContent === '强势样本')!.click())
    expect(container.querySelector('[data-testid=insight]')?.textContent).toBe('600001')
  } finally {
    act(() => root.unmount())
    container.remove()
    vi.unstubAllGlobals()
  }
})

it('从工作台打开后启动手动预览，显示进度并切换到本次结果', async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  vi.useFakeTimers()
  const container = document.body.appendChild(document.createElement('div'))
  const root = createRoot(container)
  vi.mocked(breakoutApi.startPreview).mockClear().mockResolvedValue({
    id: 'my-scan', status: 'running', progress_current: 24, progress_total: 50, result: {}, error: '',
  })
  vi.mocked(breakoutApi.listBWatchlist).mockResolvedValue({ snapshot_date: null, items: [] })
  vi.mocked(breakoutApi.getPreview).mockResolvedValue({
    id: 'my-scan', status: 'success', progress_current: 50, progress_total: 50, error: '',
    result: { snapshot_date: '2026-11-24', status: 'ok', scanned: 50, evaluated: 50, items: [] },
  })
  try {
    await act(async () => root.render(<BreakoutWatchlistPage runOnOpen />))
    expect(breakoutApi.startPreview).toHaveBeenCalledTimes(1)
    expect(container.textContent).toContain('扫描中 24/50 只')
    // Once running, the button remains disabled until the background task completes.
    const button = Array.from(container.querySelectorAll('button')).find(b => b.textContent === '手动扫描')
    expect(button?.disabled).toBe(true)
    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(container.textContent).toContain('手动预览覆盖 50/50 只')
    expect(container.textContent).toContain('手动扫描 2026-11-24')
    expect(button?.disabled).toBe(false)
  } finally {
    act(() => root.unmount())
    container.remove()
    vi.useRealTimers()
    vi.unstubAllGlobals()
  }
})
