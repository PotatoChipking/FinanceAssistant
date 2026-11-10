import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { fetchAPI } from '@panwatch/api'
import TMonitorPanel from './TMonitorPanel'

vi.mock('@panwatch/api', () => ({ fetchAPI: vi.fn() }))

const request = vi.mocked(fetchAPI)
const row = {
  id: 1,
  position_id: 1,
  stock_symbol: '600519',
  stock_name: '测试持仓',
  trade_date: '2026-11-10',
  state: 'idle',
  score: 30,
  recommended_quantity: 0,
  current_price: 10,
  vwap: 10,
  support_price: null,
  stop_loss_price: null,
  target_price: null,
}

let container: HTMLDivElement
let root: Root
const refreshQuotes = vi.fn(async () => true)

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  request.mockReset()
  refreshQuotes.mockClear()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it('定时读取新行情，读取失败时保留已显示的结果', async () => {
  request.mockResolvedValueOnce([row])
    .mockResolvedValueOnce([{ ...row, context: { minute_at: '2026-11-10 10:02' } }])
    .mockRejectedValueOnce(new Error('网络错误'))

  await act(async () => root.render(<TMonitorPanel quotes={{}} onRefreshQuotes={refreshQuotes} />))
  expect(container.textContent).toContain('行情日期 2026-11-10')

  await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
  expect(request).toHaveBeenLastCalledWith('/t-monitor/states', { cache: 'no-store' })
  expect(container.textContent).toContain('分钟行情 2026-11-10 10:02')

  await act(async () => { await vi.advanceTimersByTimeAsync(10_000) })
  expect(container.textContent).toContain('读取扫描结果失败')
  expect(container.textContent).toContain('分钟行情 2026-11-10 10:02')
})

it('显示手动扫描跳过持仓的原因', async () => {
  request.mockResolvedValueOnce([row])
    .mockResolvedValueOnce({
      scanned: 1,
      triggered: 0,
      results: [{ position_id: 1, symbol: '600519', status: 'skipped', reason: '分钟 K 线不可用' }],
    })
    .mockResolvedValueOnce([row])

  await act(async () => root.render(<TMonitorPanel quotes={{}} onRefreshQuotes={refreshQuotes} />))
  const scanButton = Array.from(container.querySelectorAll('button'))
    .find(button => button.textContent === '立即扫描')
  expect(scanButton).toBeDefined()
  await act(async () => { scanButton!.click() })

  expect(container.textContent).toContain('600519 · 分钟 K 线不可用')
  expect(refreshQuotes).toHaveBeenCalledOnce()
  expect(request.mock.calls.map(([path]) => path)).toEqual(['/t-monitor/states', '/t-monitor/scan', '/t-monitor/states'])
})

it('先显示新报价，随后显示手动扫描重算的 VWAP 和评分', async () => {
  const quote = { current_price: 10.2, data_as_of: '2026-11-10 10:02:40' }
  const onRefreshQuotes = vi.fn(async () => {
    root.render(<TMonitorPanel quotes={{ 'CN:600519': quote }} onRefreshQuotes={onRefreshQuotes} />)
    return true
  })
  type Summary = { scanned: number; triggered: number; results: [] }
  let finishScan!: (summary: Summary) => void
  const scan = new Promise<Summary>(resolve => { finishScan = resolve })
  request.mockResolvedValueOnce([{ ...row, context: { minute_at: '2026-11-10 10:01' } }])
    .mockReturnValueOnce(scan)
    .mockResolvedValueOnce([{ ...row, vwap: 10.1, score: 45, context: { minute_at: '2026-11-10 10:02' } }])

  const staleQuote = { current_price: 9, data_as_of: '2026-11-10 09:59:00' }
  await act(async () => root.render(<TMonitorPanel quotes={{ 'CN:600519': staleQuote }} onRefreshQuotes={onRefreshQuotes} />))
  expect(container.textContent).toContain('分钟收盘价10.000')
  const scanButton = Array.from(container.querySelectorAll('button'))
    .find(button => button.textContent === '立即扫描')
  await act(async () => { scanButton!.click() })
  expect(container.textContent).toContain('报价 2026-11-10 10:02:40')
  expect(container.textContent).toContain('现价10.200')
  expect(container.textContent).toContain('T Score 30')

  await act(async () => finishScan({ scanned: 1, triggered: 0, results: [] }))
  expect(container.textContent).toContain('分钟行情 2026-11-10 10:02')
  expect(container.textContent).toContain('10.100')
  expect(container.textContent).toContain('T Score 45')
})
