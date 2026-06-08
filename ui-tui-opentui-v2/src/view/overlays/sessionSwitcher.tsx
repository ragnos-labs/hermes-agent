/**
 * SessionSwitcher — pick a session to resume (spec §2b; Ink
 * `activeSessionSwitcher.tsx`). A native `<select>` over `session.list` rows;
 * Enter resumes the chosen session (the entry runs the same resume-hydrate path
 * as launch), Esc/Ctrl+C closes. Replaces the composer while open.
 */
import { useKeyboard } from '@opentui/solid'
import { createMemo } from 'solid-js'

import type { SessionItem } from '../../logic/store.ts'
import { useTheme } from '../theme.tsx'

export function SessionSwitcher(props: {
  sessions: SessionItem[]
  onPick: (sessionId: string) => void
  onClose: () => void
}) {
  const theme = useTheme()
  useKeyboard(key => {
    if (key.name === 'escape' || (key.ctrl && key.name === 'c')) props.onClose()
  })

  const options = createMemo(() =>
    props.sessions.map(s => ({
      description: `${s.messageCount} msgs${s.preview ? ` · ${s.preview.slice(0, 60)}` : ''}`,
      name: s.title || s.preview.slice(0, 48) || s.id,
      value: s.id
    }))
  )

  return (
    <box
      style={{ borderColor: theme().color.border, flexDirection: 'column', flexShrink: 0, marginTop: 1, padding: 1 }}
      border
    >
      <text fg={theme().color.accent}>
        <b>⟲ Resume a session</b>
      </text>
      <select
        focused
        options={options()}
        onSelect={(_index, option) => {
          if (option) props.onPick(String(option.value))
        }}
        backgroundColor={theme().color.statusBg}
        selectedBackgroundColor={theme().color.selectionBg}
        textColor={theme().color.text}
        selectedTextColor={theme().color.text}
        descriptionColor={theme().color.muted}
        style={{ height: Math.min(16, Math.max(2, options().length * 2)), marginTop: 1 }}
      />
      <text fg={theme().color.muted}>↑↓ select · Enter resume · Esc cancel</text>
    </box>
  )
}
