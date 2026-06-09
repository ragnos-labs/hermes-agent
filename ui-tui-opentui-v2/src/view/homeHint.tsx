/**
 * HomeHint — the empty-transcript home screen (items 12 + 9). Shows the full
 * HERMES-AGENT banner (the canonical CLI logo, width-guarded → compact brand on
 * narrow terminals), the welcome line, a COLLAPSIBLE tools/skills/MCP catalog
 * panel (item 9), the common commands, and the key tips. Fully themed; decorative,
 * so `selectable={false}` (item 4).
 */
import { useDimensions } from './dimensions.tsx'
import { createSignal, For, Show } from 'solid-js'

import type { Catalog } from '../logic/store.ts'
import { useTheme } from './theme.tsx'

const COMMANDS: ReadonlyArray<readonly [string, string]> = [
  ['/help', 'list all commands'],
  ['/model', 'switch model'],
  ['/sessions', 'resume a session'],
  ['/skills', 'browse skills'],
  ['/agents', 'live delegation trace'],
  ['/clear', 'clear the transcript']
]

// The canonical HERMES-AGENT block logo (hermes_cli/banner.py), gold→amber→bronze.
const BANNER: ReadonlyArray<readonly [string, 'primary' | 'accent' | 'border']> = [
  ['██╗  ██╗███████╗██████╗ ███╗   ███╗███████╗███████╗       █████╗  ██████╗ ███████╗███╗   ██╗████████╗', 'primary'],
  ['██║  ██║██╔════╝██╔══██╗████╗ ████║██╔════╝██╔════╝      ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝', 'primary'],
  ['███████║█████╗  ██████╔╝██╔████╔██║█████╗  ███████╗█████╗███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║', 'accent'],
  ['██╔══██║██╔══╝  ██╔══██╗██║╚██╔╝██║██╔══╝  ╚════██║╚════╝██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║', 'accent'],
  ['██║  ██║███████╗██║  ██║██║ ╚═╝ ██║███████╗███████║      ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║', 'border'],
  ['╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═╝     ╚═╝╚══════╝╚══════╝      ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝', 'border']
]
const BANNER_W = 102

export function HomeHint(props: { catalog: Catalog | undefined }) {
  const theme = useTheme()
  const dims = useDimensions()
  const [open, setOpen] = createSignal(false)
  const wide = () => dims().width >= BANNER_W
  const cat = () => props.catalog

  return (
    <box style={{ flexDirection: 'column', flexShrink: 0, paddingLeft: 1, marginTop: 1 }}>
      {/* banner — full block logo when there's room, else a compact brand line */}
      <Show
        when={wide()}
        fallback={
          <text selectable={false}>
            <span style={{ fg: theme().color.accent }}>{theme().brand.icon} </span>
            <span style={{ fg: theme().color.primary }}>
              <b>{theme().brand.name}</b>
            </span>
          </text>
        }
      >
        <For each={BANNER}>
          {([line, tone]) => (
            <text selectable={false}>
              <span style={{ fg: theme().color[tone] }}>{line}</span>
            </text>
          )}
        </For>
      </Show>

      <box style={{ marginTop: 1 }}>
        <text selectable={false}>
          <span style={{ fg: theme().color.muted }}>{theme().brand.welcome}</span>
        </text>
      </box>

      {/* collapsible tools / skills / MCP catalog (item 9) */}
      <Show when={cat()}>
        {c => (
          <box style={{ flexDirection: 'column', marginTop: 1 }}>
            <box style={{ flexDirection: 'row', flexShrink: 0 }} onMouseDown={() => setOpen(o => !o)}>
              <text selectable={false}>
                <span style={{ fg: theme().color.muted }}>{open() ? '▼ ' : '▶ '}</span>
                <span style={{ fg: theme().color.label }}>{`${c().tools.total} tools`}</span>
                <span style={{ fg: theme().color.muted }}>{' · '}</span>
                <span style={{ fg: theme().color.label }}>{`${c().skills.total} skills`}</span>
                <span style={{ fg: theme().color.muted }}>{' · '}</span>
                <span style={{ fg: theme().color.label }}>{`${c().mcp.servers.length} MCP`}</span>
              </text>
            </box>
            <Show when={open()}>
              <box
                style={{ flexDirection: 'column', marginLeft: 2, paddingLeft: 1 }}
                border={['left']}
                borderColor={theme().color.border}
              >
                <text selectable={false}>
                  <span style={{ fg: theme().color.label }}>toolsets  </span>
                  <span style={{ fg: theme().color.muted }}>
                    {c().tools.toolsets.map(t => `${t.name}(${t.count})`).join('  ')}
                  </span>
                </text>
                <Show when={c().skills.categories.length > 0}>
                  <text selectable={false}>
                    <span style={{ fg: theme().color.label }}>skills    </span>
                    <span style={{ fg: theme().color.muted }}>
                      {c().skills.categories.map(s => `${s.name}(${s.count})`).join('  ')}
                    </span>
                  </text>
                </Show>
                <Show when={c().mcp.servers.length > 0}>
                  <text selectable={false}>
                    <span style={{ fg: theme().color.label }}>mcp       </span>
                    <span style={{ fg: theme().color.muted }}>{c().mcp.servers.join('  ')}</span>
                  </text>
                </Show>
              </box>
            </Show>
          </box>
        )}
      </Show>

      <box style={{ flexDirection: 'column', marginTop: 1 }}>
        <For each={COMMANDS}>
          {([cmd, desc]) => (
            <text selectable={false}>
              <span style={{ fg: theme().color.accent }}>{cmd.padEnd(12)}</span>
              <span style={{ fg: theme().color.muted }}>{desc}</span>
            </text>
          )}
        </For>
      </box>
      <box style={{ marginTop: 1 }}>
        <text selectable={false}>
          <span style={{ fg: theme().color.muted }}>
            Type to chat · ↑↓ history · @file to mention · Ctrl+C to stop/quit
          </span>
        </text>
      </box>
    </box>
  )
}
