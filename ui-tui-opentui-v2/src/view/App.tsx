/**
 * App — the Solid view shell (spec v4 §2 `view/App.tsx`). Header + scrolling
 * transcript + an input zone that swaps the composer for a blocking-prompt
 * overlay when one is active. Fully themed via the ThemeProvider (§7.5).
 *
 *   header     flexShrink:0            (top chrome line)
 *   transcript flexGrow:1, minHeight:0 (the one <scrollbox>; §8 #2 gotchas)
 *   input zone flexShrink:0            (Composer, OR PromptOverlay when blocked)
 *
 * When `store.state.prompt` is set the composer is REPLACED by the prompt overlay
 * (so the composer's textarea no longer captures keys, and the prompt's
 * select/input/masked-buffer owns input) — the §8 #6 deadlock fix. `onSubmit`/
 * `onRespond`/`sessionId` are wired by the entry (Effect boundary); all optional
 * so headless frame tests can mount the shell without a gateway.
 */
import { Show } from 'solid-js'

import type { SessionStore } from '../logic/store.ts'
import { Composer } from './composer.tsx'
import { Header } from './header.tsx'
import { PromptOverlay } from './prompts/promptOverlay.tsx'
import { Transcript } from './transcript.tsx'

export interface AppProps {
  readonly store: SessionStore
  readonly onSubmit?: (text: string) => void
  readonly onRespond?: (method: string, params: Record<string, unknown>) => void
  readonly sessionId?: () => string | undefined
}

const NOOP = () => {}
const NOOP_RESPOND = () => {}
const NO_SESSION = () => undefined

export function App(props: AppProps) {
  const blocked = () => props.store.state.prompt !== undefined
  return (
    <box style={{ flexDirection: 'column', flexGrow: 1, padding: 1 }}>
      <Header store={props.store} />
      <Transcript store={props.store} />
      <Show when={blocked()} fallback={<Composer onSubmit={props.onSubmit ?? NOOP} />}>
        <PromptOverlay
          store={props.store}
          onRespond={props.onRespond ?? NOOP_RESPOND}
          sessionId={props.sessionId ?? NO_SESSION}
        />
      </Show>
    </box>
  )
}
