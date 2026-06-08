/**
 * ApprovalPrompt — dangerous-command approval (spec §8 #6). Native `<select>`
 * (built-in ↑↓/j/k/Enter nav) over once/session/always/deny; a small `useKeyboard`
 * adds the Esc/Ctrl+C → deny cancel path the select doesn't cover. Answered via
 * `approval.respond {choice, session_id}`.
 */
import { useKeyboard } from '@opentui/solid'

import { useTheme } from '../theme.tsx'

const OPTIONS = [
  { description: 'Run this command this one time', name: 'Approve once', value: 'once' },
  { description: 'Allow for the rest of this session', name: 'Approve for session', value: 'session' },
  { description: 'Always allow this command', name: 'Always approve', value: 'always' },
  { description: 'Reject this command', name: 'Deny', value: 'deny' }
]

export function ApprovalPrompt(props: {
  command: string
  description: string
  onChoose: (choice: string) => void
  onCancel: () => void
}) {
  const theme = useTheme()
  useKeyboard(key => {
    if (key.name === 'escape' || (key.ctrl && key.name === 'c')) props.onCancel()
  })

  return (
    <box
      style={{ borderColor: theme().color.border, flexDirection: 'column', flexShrink: 0, marginTop: 1, padding: 1 }}
      border
    >
      <text fg={theme().color.warn}>
        <b>⚠ Approval required</b>
      </text>
      <text fg={theme().color.text}>{props.command}</text>
      {props.description ? <text fg={theme().color.muted}>{props.description}</text> : null}
      <select
        focused
        options={OPTIONS}
        onSelect={(_index, option) => {
          if (option) props.onChoose(String(option.value))
        }}
        backgroundColor={theme().color.statusBg}
        selectedBackgroundColor={theme().color.selectionBg}
        textColor={theme().color.text}
        selectedTextColor={theme().color.text}
        descriptionColor={theme().color.muted}
        style={{ height: 8, marginTop: 1 }}
      />
      <text fg={theme().color.muted}>↑↓ select · Enter confirm · Esc/Ctrl+C deny</text>
    </box>
  )
}
