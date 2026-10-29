import { Layers, Loader2, RotateCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import {
  OPTION_CHAIN_METRIC_GROUPS,
  OPTION_CHAIN_METRICS,
  type OptionChainMetric,
} from '@/lib/optionChainMetrics'
import { cn } from '@/lib/utils'
import { useOptionChainOverlayContext } from './OptionChainOverlayProvider'

const STRIKE_COUNTS = [10, 15, 20] as const

function formatSpot(value: number): string {
  if (!(value > 0)) return '-'
  return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 }).format(value)
}

function stateLine({
  isPaused,
  isStreaming,
  lastUpdate,
}: {
  isPaused: boolean
  isStreaming: boolean
  lastUpdate: Date | null
}): string {
  if (isPaused) return 'Paused while this tab is hidden.'
  if (isStreaming) return 'Live prices. Open interest refreshes every 30 seconds.'
  if (lastUpdate) return `Not streaming. Last update ${lastUpdate.toLocaleTimeString()}.`
  return 'Waiting for the first chain.'
}

/**
 * The toolbar control for one pane's option-chain overlay.
 *
 * It renders from the provider's context rather than owning the primitive, so
 * it may unmount with the shared toolbar without taking the overlay with it.
 */
export function OptionChainOverlayControl({ menuHost }: { menuHost?: HTMLElement | null }) {
  const { available, active, settings, state, setActive, setSettings } =
    useOptionChainOverlayContext()
  if (!available) return null

  const loading = state.isLoading && !state.data
  const failed = state.status === 'unavailable' && !state.data

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className={cn('h-8 shrink-0 gap-1', active && 'border-primary text-primary')}
          title="Option chain overlay"
        >
          <Layers className="h-4 w-4" />
          <span className="hidden sm:inline">Chain</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent container={menuHost} align="start" className="w-80 p-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm font-medium">Option chain</p>
            <p className="truncate text-[11px] text-muted-foreground">
              {state.identity
                ? `${state.identity.underlying} · ${state.expiry || 'No expiry'}`
                : 'Reading this chart'}
            </p>
          </div>
          <Switch
            checked={active}
            onCheckedChange={setActive}
            aria-label="Show the option chain overlay"
          />
        </div>

        {!active && (
          <p className="mt-2 text-[11px] text-muted-foreground">
            Turn this on to draw the chain beside the price axis.
          </p>
        )}

        {loading && (
          <p className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Reading the chain…
          </p>
        )}

        {failed && (
          <div className="mt-3 rounded-md border border-destructive/30 bg-destructive/5 p-2">
            <p className="text-xs text-destructive">
              {state.error ?? 'The option chain is not available for this chart.'}
            </p>
            <Button
              variant="outline"
              size="sm"
              className="mt-2 h-7 gap-1 text-xs"
              onClick={state.retry}
            >
              <RotateCw className="h-3 w-3" />
              Try again
            </Button>
          </div>
        )}

        {state.data && (
          <p className="mt-2 text-[11px] text-muted-foreground">
            Spot {formatSpot(state.data.spot)} · PCR {state.data.pcr.toFixed(2)}
          </p>
        )}

        {state.error && state.data && (
          <p className="mt-2 text-[11px] text-amber-600 dark:text-amber-400">
            {state.error}{' '}
            <button type="button" className="underline underline-offset-2" onClick={state.retry}>
              Retry
            </button>
          </p>
        )}

        {state.identity && (
          <div className="mt-3 space-y-3 border-t pt-3">
            <div className="space-y-1">
              <span className="text-[11px] text-muted-foreground">Expiry</span>
              <Select
                value={state.expiry || undefined}
                onValueChange={state.setExpiry}
                disabled={state.expiries.length === 0}
              >
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue placeholder="Expiry" />
                </SelectTrigger>
                <SelectContent container={menuHost}>
                  {state.expiries.map((expiry) => (
                    <SelectItem key={expiry} value={expiry} className="text-xs">
                      {expiry}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1">
              <span className="text-[11px] text-muted-foreground">Show</span>
              <Select
                value={settings.metric}
                onValueChange={(value) => setSettings({ metric: value as OptionChainMetric })}
              >
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent container={menuHost}>
                  {OPTION_CHAIN_METRIC_GROUPS.map((group) => (
                    <SelectGroup key={group}>
                      <SelectLabel>{group}</SelectLabel>
                      {OPTION_CHAIN_METRICS.filter((metric) => metric.group === group).map(
                        (metric) => (
                          <SelectItem key={metric.id} value={metric.id} className="text-xs">
                            {metric.label}
                          </SelectItem>
                        )
                      )}
                    </SelectGroup>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1">
              <span className="text-[11px] text-muted-foreground">Strikes</span>
              <Select
                value={String(settings.strikeCount)}
                onValueChange={(value) => setSettings({ strikeCount: Number(value) })}
              >
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent container={menuHost}>
                  {STRIKE_COUNTS.map((count) => (
                    <SelectItem key={count} value={String(count)} className="text-xs">
                      {count} strikes
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex items-center justify-between gap-3">
              <span className="text-xs">Value labels</span>
              <Switch
                checked={settings.showValues}
                onCheckedChange={(value) => setSettings({ showValues: value })}
                aria-label="Show value labels on the overlay bars"
              />
            </div>

            <div className="flex items-center justify-between gap-3">
              <span className="text-xs">Support / resistance</span>
              <Switch
                checked={settings.showLevels}
                onCheckedChange={(value) => setSettings({ showLevels: value })}
                aria-label="Show support and resistance lines"
              />
            </div>
          </div>
        )}

        {active && state.status === 'ready' && (
          <p className="mt-3 border-t pt-2 text-[11px] text-muted-foreground">{stateLine(state)}</p>
        )}
      </PopoverContent>
    </Popover>
  )
}
