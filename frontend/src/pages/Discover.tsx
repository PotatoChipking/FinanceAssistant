import { useState } from 'react'
import { SlidersHorizontal, Sparkles, TrendingUp } from 'lucide-react'
import BreakoutWatchlistPage from '@/pages/BreakoutWatchlist'
import ScreenerPage from '@/pages/Screener'
import OpportunitiesPage from '@/pages/Opportunities'

type DiscoverTab = 'screener' | 'opportunities' | 'breakout'

const TABS: { key: DiscoverTab; label: string; icon: typeof SlidersHorizontal }[] = [
  { key: 'screener', label: '选股', icon: SlidersHorizontal },
  { key: 'opportunities', label: '机会', icon: Sparkles },
  { key: 'breakout', label: 'B 类观察', icon: TrendingUp },
]

export default function DiscoverPage({ initialTab = 'screener' }: { initialTab?: DiscoverTab }) {
  const [tab, setTab] = useState<DiscoverTab>(initialTab)
  const [runBreakoutOnOpen, setRunBreakoutOnOpen] = useState(false)
  return (
    <div>
      <div className="mb-4">
        <div className="inline-flex items-center gap-1 p-1 rounded-lg bg-accent/30">
          {TABS.map(({ key, label, icon: Icon }) => (
            <button
              type="button"
              key={key}
              aria-pressed={tab === key}
              onClick={() => {
                if (key === tab) return
                setRunBreakoutOnOpen(false)
                setTab(key)
              }}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[12px] transition-colors ${
                tab === key ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              {label}
            </button>
          ))}
        </div>
      </div>
      {tab === 'screener' && <ScreenerPage onRunStrongB={() => {
        setRunBreakoutOnOpen(true)
        setTab('breakout')
      }} />}
      {tab === 'opportunities' && <OpportunitiesPage />}
      {tab === 'breakout' && <BreakoutWatchlistPage runOnOpen={runBreakoutOnOpen} />}
    </div>
  )
}
