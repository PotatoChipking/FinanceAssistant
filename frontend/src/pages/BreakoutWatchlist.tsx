import { useCallback, useEffect, useRef, useState } from 'react'
import { Play, RefreshCw } from 'lucide-react'
import { breakoutApi, type BreakoutPreviewTask, type BreakoutWatchItem, type BreakoutWatchlist } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import StockInsightModal from '@panwatch/biz-ui/components/stock-insight-modal'

function percent(value: number) {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

export default function BreakoutWatchlistPage({ runOnOpen = false }: { runOnOpen?: boolean }) {
  const [data, setData] = useState<BreakoutWatchlist | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [strongOnly, setStrongOnly] = useState(runOnOpen)
  const [selected, setSelected] = useState<BreakoutWatchItem | null>(null)
  const [task, setTask] = useState<BreakoutPreviewTask | null>(null)
  const [starting, setStarting] = useState(false)
  const [scanError, setScanError] = useState('')
  const [showPreview, setShowPreview] = useState(false)
  const startedOnOpen = useRef(false)
  const launchRequested = useRef(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setData(await breakoutApi.listBWatchlist())
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '观察池加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const startPreview = useCallback(async () => {
    launchRequested.current = true
    setStarting(true)
    setScanError('')
    setShowPreview(true)
    try {
      setTask(await breakoutApi.startPreview())
    } catch (cause) {
      setShowPreview(false)
      setScanError(cause instanceof Error ? cause.message : '启动扫描失败')
    } finally {
      setStarting(false)
    }
  }, [])

  useEffect(() => {
    if (runOnOpen) {
      if (!startedOnOpen.current) {
        startedOnOpen.current = true
        void startPreview()
      }
      return
    }
    let mounted = true
    breakoutApi.latestPreview().then(({ items }) => {
      if (!mounted || launchRequested.current || !items[0]) return
      setTask(items[0])
      setShowPreview(items[0].status === 'queued' || items[0].status === 'running')
    }).catch(() => {})
    return () => { mounted = false }
  }, [runOnOpen, startPreview])

  const taskId = task?.id
  const scanning = task?.status === 'queued' || task?.status === 'running'
  useEffect(() => {
    if (!taskId || !scanning) return
    let mounted = true
    let timer: number
    let failures = 0
    const poll = async () => {
      try {
        const latest = await breakoutApi.getPreview(taskId)
        if (!mounted) return
        failures = 0
        setTask(latest)
        setScanError('')
        if (latest.status !== 'queued' && latest.status !== 'running') return
      } catch {
        if (!mounted) return
        failures += 1
        if (failures >= 3) {
          setTask(current => current?.id === taskId
            ? { ...current, status: 'failed', error: '无法读取手动扫描结果，可以重新扫描。' }
            : current)
          setScanError('')
          return
        }
        setScanError('暂时无法读取扫描进度，正在重试。')
      }
      timer = window.setTimeout(poll, 2000)
    }
    timer = window.setTimeout(poll, 1500)
    return () => {
      mounted = false
      window.clearTimeout(timer)
    }
  }, [taskId, scanning])

  const previewResult = showPreview && task?.status === 'success' ? task.result : null
  const allItems = previewResult?.items ?? data?.items ?? []
  const snapshotDate = previewResult ? previewResult.snapshot_date : data?.snapshot_date
  const strongItems = allItems.filter((item) => item.strength?.strong)
  const items = strongOnly ? strongItems : allItems

  return (
    <section aria-labelledby="breakout-watchlist-title" className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 id="breakout-watchlist-title" className="text-[20px] font-bold tracking-tight">B 类观察池</h1>
          <p className="mt-1 text-xs text-muted-foreground">
            强势 B 的行业和个股同行排名均进入前 1/3。收益取突破前 20 个交易日，行业采用扫描时的东方财富分类。
          </p>
        </div>
        <div className="flex items-center gap-2">
          {snapshotDate && (
            <span className="text-xs text-muted-foreground">{previewResult ? '手动扫描' : '盘后快照'} {snapshotDate}</span>
          )}
          <Button variant="secondary" size="sm" onClick={() => { setShowPreview(false); void load() }} disabled={loading}>
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            读取盘后快照
          </Button>
          <Button size="sm" onClick={startPreview} disabled={starting || scanning}>
            <Play className="mr-1.5 h-3.5 w-3.5" />
            手动扫描
          </Button>
        </div>
      </div>

      <div className="flex items-center gap-2" aria-label="B 类候选筛选">
        <Button variant={strongOnly ? 'ghost' : 'secondary'} size="sm" aria-pressed={!strongOnly}
          onClick={() => setStrongOnly(false)}>
          全部 B 类 {allItems.length}
        </Button>
        <Button variant={strongOnly ? 'secondary' : 'ghost'} size="sm" aria-pressed={strongOnly}
          onClick={() => setStrongOnly(true)}>
          强势 B {strongItems.length}
        </Button>
      </div>

      {(starting || scanning || previewResult) && (
        <p role="status" className="card p-3 text-sm text-muted-foreground">
          {starting ? '正在启动手动扫描…' : scanning ? (
            task.progress_total ? `扫描中 ${task.progress_current}/${task.progress_total} 只` : '正在准备主板股票清单…'
          ) : previewResult?.status === 'data_unavailable' ? '没有可用的日线数据，无法生成手动预览。'
            : `手动预览覆盖 ${previewResult?.evaluated ?? 0}/${previewResult?.scanned ?? 0} 只。结果仅用于观察。`}
        </p>
      )}
      {task?.status === 'success' && !showPreview && !starting && !scanning && (
        <Button variant="ghost" size="sm" onClick={() => setShowPreview(true)}>查看手动结果</Button>
      )}
      {(scanError || task?.status === 'failed' || task?.status === 'stale') && (
        <p role="alert" className="card p-3 text-sm text-amber-600 dark:text-amber-400">
          {scanError || (task?.status === 'stale' ? '手动扫描超时，请重试。' : task?.error || '手动扫描失败')}
        </p>
      )}
      {error && !previewResult && <p role="alert" className="card p-3 text-sm text-amber-600 dark:text-amber-400">{error}</p>}
      {loading && !data && <p role="status" className="card p-6 text-sm text-muted-foreground">正在加载观察池…</p>}
      {!loading && !starting && !scanning && (!error || previewResult)
        && previewResult?.status !== 'data_unavailable' && items.length === 0 && (
        <p className="card p-6 text-sm text-muted-foreground">
          {strongOnly ? '当前快照暂无强势 B' : snapshotDate ? '当前快照暂无 B 类候选' : '等待突破扫描生成快照'}
        </p>
      )}
      {items.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[610px] text-left text-sm" aria-label="B 类候选排名">
            <thead className="border-b border-border bg-accent/30 text-xs text-muted-foreground">
              <tr>
                <th scope="col" className="px-4 py-3 font-medium">股票</th>
                <th scope="col" className="px-4 py-3 font-medium">突破日</th>
                <th scope="col" className="px-4 py-3 font-medium">行业排名</th>
                <th scope="col" className="px-4 py-3 font-medium">个股同行排名</th>
                <th scope="col" className="px-4 py-3 font-medium">强势标签</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {items.map((item) => {
                const strength = item.strength
                return (
                  <tr key={item.id ?? item.symbol} className="hover:bg-accent/25">
                    <td className="px-4 py-3">
                      <button type="button" className="font-medium hover:text-primary hover:underline"
                        onClick={() => setSelected(item)}>
                        {item.name || item.symbol}
                      </button>
                      {item.name && <div className="text-xs text-muted-foreground">{item.symbol}</div>}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap">
                      {item.breakout_date || '—'}
                      {item.breakout_level != null && (
                        <div className="text-xs text-muted-foreground">前高 {item.breakout_level}</div>
                      )}
                    </td>
                    <td className="px-4 py-3" title={strength ? `行业快照 ${strength.industry_as_of}` : undefined}>
                      {strength ? (
                        <>
                          <div>{strength.industry}</div>
                          <div className="whitespace-nowrap text-xs text-muted-foreground">
                            {strength.industry_rank}/{strength.industry_count} · {percent(strength.industry_return_pct)}
                          </div>
                        </>
                      ) : '—'}
                    </td>
                    <td className="px-4 py-3" title={strength ? `收益截至 ${strength.price_as_of}` : undefined}>
                      {strength ? (
                        <>
                          <div>{strength.stock_rank}/{strength.peer_count}</div>
                          <div className="text-xs text-muted-foreground">{percent(strength.stock_return_pct)}</div>
                        </>
                      ) : '—'}
                    </td>
                    <td className="px-4 py-3 whitespace-nowrap">
                      {strength?.strong ? (
                        <span className="rounded-full bg-primary/10 px-2 py-1 text-xs font-medium text-primary">强势 B</span>
                      ) : (
                        <span className="text-xs text-muted-foreground">{strength ? 'B 类' : '强度未评估'}</span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {selected && <StockInsightModal
        open
        onOpenChange={(open) => { if (!open) setSelected(null) }}
        symbol={selected.symbol}
        market="CN"
        stockName={selected.name || undefined}
      />}
    </section>
  )
}
