import { describe, expect, it } from 'vitest'

import {
  cronEditorUpdates,
  jobIsScriptOnly,
  parseCronDeliveryTargets,
  toggleCronDeliveryTarget,
  validateCronEditor
} from './cron-job-model'

describe('jobIsScriptOnly', () => {
  it('is true when no_agent is set and a script is present', () => {
    expect(jobIsScriptOnly({ no_agent: true, script: 'echo hi' })).toBe(true)
  })

  it('is false for agent-backed jobs', () => {
    expect(jobIsScriptOnly({ no_agent: false, script: 'echo hi' })).toBe(false)
    expect(jobIsScriptOnly({ no_agent: true, script: '' })).toBe(false)
    expect(jobIsScriptOnly({ no_agent: true, script: null })).toBe(false)
  })
})

describe('validateCronEditor', () => {
  it('requires prompt and schedule for agent-backed jobs', () => {
    expect(validateCronEditor({ prompt: '', schedule: '', scriptOnlyJob: false })).toBe('prompt_and_schedule')
    expect(validateCronEditor({ prompt: '', schedule: '0 9 * * *', scriptOnlyJob: false })).toBe('prompt')
    expect(validateCronEditor({ prompt: 'go', schedule: '', scriptOnlyJob: false })).toBe('schedule')
  })

  it('allows an empty prompt when editing a script-only job', () => {
    expect(validateCronEditor({ prompt: '', schedule: '0 9 * * 1', scriptOnlyJob: true })).toBe(null)
    expect(validateCronEditor({ prompt: 'optional note', schedule: '0 9 * * 1', scriptOnlyJob: true })).toBe(null)
  })

  it('still requires schedule for script-only jobs', () => {
    expect(validateCronEditor({ prompt: '', schedule: '', scriptOnlyJob: true })).toBe('schedule')
  })
})

describe('cron delivery targets', () => {
  it('parses comma-separated targets and removes duplicates', () => {
    expect(parseCronDeliveryTargets('local, telegram,local')).toEqual(['local', 'telegram'])
  })

  it('falls back to local for an empty stored value', () => {
    expect(parseCronDeliveryTargets('')).toEqual(['local'])
  })

  it('adds a second target in the scheduler comma-separated format', () => {
    expect(toggleCronDeliveryTarget('local', 'origin', true)).toBe('local,origin')
  })

  it('removes one target while keeping the other selection', () => {
    expect(toggleCronDeliveryTarget('local,origin', 'local', false)).toBe('origin')
  })

  it('does not allow the final delivery target to be unchecked', () => {
    expect(toggleCronDeliveryTarget('origin', 'origin', false)).toBe('origin')
  })
})

describe('cronEditorUpdates', () => {
  it('omits prompt when saving a script-only job with an empty prompt', () => {
    expect(
      cronEditorUpdates(
        { deliver: 'local', model: '', name: 'Weekly', prompt: '', provider: '', schedule: '0 9 * * 1' },
        { scriptOnlyJob: true, shownName: 'Weekly' }
      )
    ).toEqual({
      deliver: 'local',
      schedule: '0 9 * * 1'
    })
  })

  it('includes prompt when the user typed one on a script-only job', () => {
    expect(
      cronEditorUpdates(
        { deliver: 'email', model: '', name: 'Weekly', prompt: 'note', provider: '', schedule: '0 9 * * 1' },
        { scriptOnlyJob: true, shownName: 'Weekly' }
      ).prompt
    ).toBe('note')
  })

  it('writes the model override for agent jobs', () => {
    const updates = cronEditorUpdates(
      {
        deliver: 'local',
        model: 'claude-sonnet-4',
        name: 'Daily',
        prompt: 'go',
        provider: 'anthropic',
        schedule: '0 9 * * *'
      },
      { scriptOnlyJob: false, shownName: 'Daily' }
    )

    expect(updates.model).toBe('claude-sonnet-4')
    expect(updates.provider).toBe('anthropic')
  })

  it('clears a previous pin when the override is reset to default', () => {
    const updates = cronEditorUpdates(
      { deliver: 'local', model: '', name: 'Daily', prompt: 'go', provider: '', schedule: '0 9 * * *' },
      { scriptOnlyJob: false, shownName: 'Daily' }
    )

    expect(updates.model).toBe(null)
    expect(updates.provider).toBe(null)
  })

  it('never touches model fields on script-only jobs', () => {
    const updates = cronEditorUpdates(
      { deliver: 'local', model: 'x', name: 'Weekly', prompt: '', provider: 'y', schedule: '0 9 * * 1' },
      { scriptOnlyJob: true, shownName: 'Weekly' }
    )

    expect('model' in updates).toBe(false)
    expect('provider' in updates).toBe(false)
  })

  // The shown name of an unnamed job is the first 50 characters of its prompt.
  const derived = 'sk-live-7f3a9c SECRET_PROMPT_MARKER rotate the va'

  const edit = (name: string, shownName: string) =>
    cronEditorUpdates(
      { deliver: 'local', model: '', name, prompt: 'edited prompt', provider: '', schedule: '0 9 * * *' },
      { scriptOnlyJob: false, shownName }
    )

  it('omits an unedited name, so a stale derived name is never re-sent', () => {
    const updates = edit(derived, derived)

    expect('name' in updates).toBe(false)
    expect(updates.prompt).toBe('edited prompt')
  })

  it('omits a name that differs from the shown one only by trimmed edges', () => {
    // JavaScript trim removes U+FEFF; Python str.strip does not.
    expect('name' in edit(derived, `\ufeff${derived}`)).toBe(false)
    expect('name' in edit(derived, `  ${derived}\u3000`)).toBe(false)
    expect('name' in edit(` ${derived} `, derived)).toBe(false)
  })

  it('sends a real rename, including a case-only one', () => {
    expect(edit('vault rotation', derived).name).toBe('vault rotation')
    expect(edit('Vault Rotation', 'vault rotation').name).toBe('Vault Rotation')
  })

  it('sends a cleared name so the server can clear the marker', () => {
    expect(edit('', 'vault rotation').name).toBe('')
  })
})
