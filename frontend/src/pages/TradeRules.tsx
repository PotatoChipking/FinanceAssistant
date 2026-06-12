import { useEffect, useMemo, useState } from 'react'
import { RotateCcw, Save, ShieldCheck, SlidersHorizontal } from 'lucide-react'
import { recommendationsApi, type TradeRulesConfig } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Switch } from '@panwatch/base-ui/components/ui/switch'
import { Badge } from '@panwatch/base-ui/components/ui/badge'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

type Field = {
  path: string
  label: string
  step?: string
  hint?: string
}

type Indicator = {
  key: string
  label: string
  fields: Array<Omit<Field, 'path'> & { field: string }>
}

const indicators: Indicator[] = [
  { key: 'trend', label: '均线趋势', fields: [{ field: 'bullish', label: '多头' }, { field: 'bearish', label: '空头' }] },
  { key: 'macd', label: 'MACD', fields: [{ field: 'golden', label: '金叉' }, { field: 'dead', label: '死叉' }, { field: 'hist_positive', label: '柱体正' }, { field: 'hist_negative', label: '柱体负' }] },
  { key: 'rsi', label: 'RSI', fields: [{ field: 'oversold', label: '超卖' }, { field: 'strong', label: '偏强' }, { field: 'overbought', label: '超买' }, { field: 'weak', label: '偏弱' }] },
  { key: 'kdj', label: 'KDJ', fields: [{ field: 'golden', label: '金叉' }, { field: 'dead', label: '死叉' }] },
  { key: 'boll', label: 'BOLL', fields: [{ field: 'break_upper', label: '突破上轨' }, { field: 'break_lower', label: '跌破下轨' }] },
  { key: 'volume', label: '量能', fields: [{ field: 'high', label: '放量' }, { field: 'low', label: '缩量' }] },
  { key: 'support_resistance', label: '支撑/压力', fields: [{ field: 'near_support', label: '近支撑' }, { field: 'near_resistance', label: '近压力' }, { field: 'support_pct', label: '支撑距离', step: '0.005' }, { field: 'resistance_pct', label: '压力距离', step: '0.005' }] },
]

const unheldThresholds: Field[] = [
  { path: 'technical.thresholds.unheld_buy', label: '未持仓买入' },
  { path: 'technical.thresholds.unheld_avoid', label: '未持仓回避' },
]

const heldThresholds: Field[] = [
  { path: 'technical.thresholds.held_add', label: '已持仓加仓' },
  { path: 'technical.thresholds.held_hold', label: '已持仓持有' },
  { path: 'technical.thresholds.held_reduce', label: '已持仓减仓' },
  { path: 'technical.thresholds.held_sell', label: '已持仓卖出' },
]

const activeFields: Field[] = [
  { path: 'opportunity.active.watchlist_min_score', label: '关注池 active 分数线' },
  { path: 'opportunity.active.market_scan_min_score', label: '市场池 active 分数线' },
  { path: 'opportunity.active.plan_quality_min', label: '入场计划质量下限' },
]

const actionScoreFields: Field[] = [
  { path: 'opportunity.action_base_scores.buy', label: '买入基础分' },
  { path: 'opportunity.action_base_scores.add', label: '加仓基础分' },
  { path: 'opportunity.action_base_scores.hold', label: '持有基础分' },
  { path: 'opportunity.action_base_scores.watch', label: '观望基础分' },
  { path: 'opportunity.action_base_scores.reduce', label: '减仓基础分' },
  { path: 'opportunity.action_base_scores.sell', label: '卖出基础分' },
  { path: 'opportunity.action_base_scores.avoid', label: '回避基础分' },
]

const riskFields: Field[] = [
  { path: 'risk.entry_band_pct', label: '入场区间上下浮动', step: '0.005', hint: '0.01 = 上下 1%' },
  { path: 'risk.buy_stop_support_discount', label: '建仓支撑止损折扣', step: '0.005' },
  { path: 'risk.hold_stop_support_discount', label: '持有支撑止损折扣', step: '0.005' },
  { path: 'risk.target_resistance_discount', label: '压力位止盈折扣', step: '0.005' },
  { path: 'risk.buy_fallback_stop_price_pct', label: '建仓兜底止损价比例', step: '0.005' },
  { path: 'risk.buy_fallback_target_price_pct', label: '建仓兜底止盈价比例', step: '0.005' },
  { path: 'risk.hold_fallback_stop_price_pct', label: '持有兜底止损价比例', step: '0.005' },
  { path: 'risk.hold_fallback_target_price_pct', label: '持有兜底止盈价比例', step: '0.005' },
  { path: 'risk.paper_fallback_stop_loss_pct', label: '模拟盘默认止损比例', step: '0.005', hint: '0.08 = 8%' },
  { path: 'risk.paper_fallback_target_profit_pct', label: '模拟盘默认止盈比例', step: '0.005', hint: '0.15 = 15%' },
]

const cloneRules = (rules: TradeRulesConfig): TradeRulesConfig =>
  JSON.parse(JSON.stringify(rules)) as TradeRulesConfig

const getValue = (rules: TradeRulesConfig | null, path: string): number => {
  let node: any = rules
  for (const part of path.split('.')) node = node?.[part]
  return typeof node === 'number' && Number.isFinite(node) ? node : 0
}

const setValue = (rules: TradeRulesConfig, path: string, value: number): TradeRulesConfig => {
  const next = cloneRules(rules)
  const parts = path.split('.')
  let node: any = next
  for (const part of parts.slice(0, -1)) node = node[part]
  node[parts[parts.length - 1]] = Number.isFinite(value) ? value : 0
  return next
}

function NumberField({
  rules,
  field,
  onChange,
}: {
  rules: TradeRulesConfig
  field: Field
  onChange: (path: string, value: number) => void
}) {
  return (
    <label className="grid grid-cols-[minmax(0,1fr)_8.5rem] items-center gap-3 rounded-lg border border-border/50 bg-background/45 px-3 py-2">
      <span className="min-w-0">
        <span className="block truncate text-[12px] font-medium text-foreground">{field.label}</span>
        {field.hint && <span className="block truncate text-[10px] text-muted-foreground">{field.hint}</span>}
      </span>
      <Input
        type="number"
        step={field.step || '1'}
        value={String(getValue(rules, field.path))}
        onChange={(e) => onChange(field.path, Number(e.target.value))}
        className="h-8 text-right font-mono text-[12px]"
      />
    </label>
  )
}

export default function TradeRulesPage() {
  const { toast } = useToast()
  const [rules, setRules] = useState<TradeRulesConfig | null>(null)
  const [defaults, setDefaults] = useState<TradeRulesConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setLoading(true)
    recommendationsApi.getTradeRules()
      .then((res) => {
        setRules(res.rules)
        setDefaults(res.defaults)
      })
      .catch((e) => toast(e instanceof Error ? e.message : '加载交易规则失败', 'error'))
      .finally(() => setLoading(false))
  }, [toast])

  const preview = useMemo(() => {
    if (!rules) return []
    return [
      `技术分: 未持仓 >= ${getValue(rules, 'technical.thresholds.unheld_buy')} 买入，<= ${getValue(rules, 'technical.thresholds.unheld_avoid')} 回避`,
      `持仓: >= ${getValue(rules, 'technical.thresholds.held_add')} 加仓，<= ${getValue(rules, 'technical.thresholds.held_sell')} 卖出`,
      `机会池 active: 关注池 ${getValue(rules, 'opportunity.active.watchlist_min_score')}，市场池 ${getValue(rules, 'opportunity.active.market_scan_min_score')}`,
      `模拟盘兜底: 止损 ${(getValue(rules, 'risk.paper_fallback_stop_loss_pct') * 100).toFixed(1)}%，止盈 ${(getValue(rules, 'risk.paper_fallback_target_profit_pct') * 100).toFixed(1)}%`,
    ]
  }, [rules])

  const updateNumber = (path: string, value: number) => {
    setRules((current) => current ? setValue(current, path, value) : current)
  }

  const updateIndicatorEnabled = (key: string, enabled: boolean) => {
    setRules((current) => {
      if (!current) return current
      const next = cloneRules(current)
      next.technical.indicators[key].enabled = enabled
      return next
    })
  }

  const updateIndicatorNumber = (key: string, field: string, value: number) => {
    updateNumber(`technical.indicators.${key}.${field}`, value)
  }

  const handleSave = async () => {
    if (!rules) return
    setSaving(true)
    try {
      const res = await recommendationsApi.updateTradeRules(rules)
      setRules(res.rules)
      setDefaults(res.defaults)
      toast('交易规则已保存，刷新机会/策略信号后生效', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '保存交易规则失败', 'error')
    } finally {
      setSaving(false)
    }
  }

  const handleReset = async () => {
    setSaving(true)
    try {
      const res = await recommendationsApi.resetTradeRules()
      setRules(res.rules)
      setDefaults(res.defaults)
      toast('已恢复默认交易规则', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : '恢复默认失败', 'error')
    } finally {
      setSaving(false)
    }
  }

  const handleUseDefaults = () => {
    if (defaults) setRules(cloneRules(defaults))
  }

  if (loading || !rules) {
    return (
      <div className="mx-auto max-w-6xl">
        <section className="card p-6 text-[13px] text-muted-foreground">正在加载交易规则...</section>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-6xl space-y-4">
      <section className="card p-4 md:p-5">
        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <SlidersHorizontal className="h-5 w-5 text-primary" />
              <h1 className="text-[20px] font-bold text-foreground">交易规则</h1>
              <Badge variant="secondary">全局</Badge>
            </div>
            <p className="mt-1 text-[12px] text-muted-foreground">
              这些规则会影响技术徽章、机会池候选、策略信号和模拟盘。保存后刷新机会/策略信号后生效。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="secondary" size="sm" onClick={handleUseDefaults} disabled={saving || !defaults}>
              <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
              填入默认
            </Button>
            <Button variant="secondary" size="sm" onClick={handleReset} disabled={saving}>
              <ShieldCheck className="mr-1.5 h-3.5 w-3.5" />
              恢复默认
            </Button>
            <Button size="sm" onClick={handleSave} disabled={saving}>
              <Save className="mr-1.5 h-3.5 w-3.5" />
              保存
            </Button>
          </div>
        </div>
      </section>

      <section className="card p-4 md:p-5">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="text-[14px] font-semibold text-foreground">指标权重</h2>
          <span className="text-[11px] text-muted-foreground">关闭后该指标不参与技术徽章评分</span>
        </div>
        <div className="space-y-3">
          {indicators.map((item) => {
            const config = rules.technical.indicators[item.key]
            return (
              <div key={item.key} className="rounded-lg border border-border/60 bg-background/35 p-3">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div className="text-[13px] font-semibold text-foreground">{item.label}</div>
                  <Switch checked={config?.enabled !== false} onCheckedChange={(v) => updateIndicatorEnabled(item.key, v)} />
                </div>
                <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-4">
                  {item.fields.map((field) => (
                    <label key={field.field} className="grid grid-cols-[minmax(0,1fr)_7rem] items-center gap-2">
                      <span className="truncate text-[12px] text-muted-foreground">{field.label}</span>
                      <Input
                        type="number"
                        step={field.step || '1'}
                        value={String(Number(config?.[field.field] ?? 0))}
                        onChange={(e) => updateIndicatorNumber(item.key, field.field, Number(e.target.value))}
                        className="h-8 text-right font-mono text-[12px]"
                      />
                    </label>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      </section>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <section className="card p-4 md:p-5">
          <h2 className="mb-3 text-[14px] font-semibold text-foreground">动作阈值</h2>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            {[...unheldThresholds, ...heldThresholds].map((field) => (
              <NumberField key={field.path} rules={rules} field={field} onChange={updateNumber} />
            ))}
          </div>
        </section>

        <section className="card p-4 md:p-5">
          <h2 className="mb-3 text-[14px] font-semibold text-foreground">机会池阈值</h2>
          <div className="grid grid-cols-1 gap-2">
            {activeFields.map((field) => (
              <NumberField key={field.path} rules={rules} field={field} onChange={updateNumber} />
            ))}
          </div>
        </section>
      </div>

      <section className="card p-4 md:p-5">
        <h2 className="mb-3 text-[14px] font-semibold text-foreground">动作基础分</h2>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-4">
          {actionScoreFields.map((field) => (
            <NumberField key={field.path} rules={rules} field={field} onChange={updateNumber} />
          ))}
        </div>
      </section>

      <section className="card p-4 md:p-5">
        <h2 className="mb-3 text-[14px] font-semibold text-foreground">风控参数</h2>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {riskFields.map((field) => (
            <NumberField key={field.path} rules={rules} field={field} onChange={updateNumber} />
          ))}
        </div>
      </section>

      <section className="card p-4 md:p-5">
        <h2 className="mb-3 text-[14px] font-semibold text-foreground">规则预览</h2>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {preview.map((line) => (
            <div key={line} className="rounded-lg border border-border/50 bg-background/45 px-3 py-2 text-[12px] text-muted-foreground">
              {line}
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
