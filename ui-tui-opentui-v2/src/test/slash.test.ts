/**
 * Slash dispatch test (spec §5 Layer 3/4). Pure logic: parse + the dispatch
 * ladder (client → slash.exec → command.dispatch) against a fake SlashContext.
 */
import { describe, expect, test } from 'bun:test'

import { dispatchSlash, parseSlash, type SlashContext } from '../logic/slash.ts'
import type { SessionItem } from '../logic/store.ts'

const FAKE_SESSIONS: SessionItem[] = [{ id: 's1', messageCount: 5, preview: 'hello there', title: 'First chat' }]

describe('parseSlash', () => {
  test('splits name + arg; rejects non-slash / empty', () => {
    expect(parseSlash('/help')).toEqual({ name: 'help', arg: '' })
    expect(parseSlash('/model anthropic/claude')).toEqual({ name: 'model', arg: 'anthropic/claude' })
    expect(parseSlash('hello')).toBeNull()
    expect(parseSlash('/')).toBeNull()
  })
})

interface Probe {
  ctx: SlashContext
  calls: Array<{ method: string; params: Record<string, unknown> }>
  system: string[]
  submitted: string[]
  confirmed: Array<{ message: string; onConfirm: () => void }>
  paged: Array<{ title: string; text: string }>
  switched: SessionItem[][]
  quit: { value: boolean }
  cleared: { value: boolean }
}

function makeCtx(request: (method: string, params: Record<string, unknown>) => Promise<unknown>): Probe {
  const calls: Probe['calls'] = []
  const system: string[] = []
  const submitted: string[] = []
  const confirmed: Probe['confirmed'] = []
  const paged: Probe['paged'] = []
  const switched: Probe['switched'] = []
  const quit = { value: false }
  const cleared = { value: false }
  const ctx: SlashContext = {
    clearTranscript: () => (cleared.value = true),
    confirm: (message, onConfirm) => confirmed.push({ message, onConfirm }),
    listSessions: () => Promise.resolve(FAKE_SESSIONS),
    logTail: () => ['gateway: spawned', 'bootstrap: session created'],
    openPager: (title, text) => paged.push({ text, title }),
    openSwitcher: sessions => switched.push(sessions),
    pushSystem: text => system.push(text),
    quit: () => (quit.value = true),
    request: (method, params) => {
      calls.push({ method, params })
      return request(method, params)
    },
    sessionId: () => 'sid-1',
    submit: text => submitted.push(text)
  }
  return { calls, cleared, confirmed, ctx, paged, quit, submitted, switched, system }
}

describe('dispatchSlash — client commands', () => {
  test('/quit quits without hitting the gateway', async () => {
    const p = makeCtx(async () => ({}))
    await dispatchSlash('/quit', p.ctx)
    expect(p.quit.value).toBe(true)
    expect(p.calls).toHaveLength(0)
  })

  test('/clear opens a confirm; running onConfirm clears the transcript', async () => {
    const p = makeCtx(async () => ({}))
    await dispatchSlash('/clear', p.ctx)
    expect(p.confirmed).toHaveLength(1)
    expect(p.cleared.value).toBe(false)
    p.confirmed[0]!.onConfirm()
    expect(p.cleared.value).toBe(true)
  })

  test('/logs opens the pager with the recent ring lines', async () => {
    const p = makeCtx(async () => ({}))
    await dispatchSlash('/logs', p.ctx)
    expect(p.paged[0]?.title).toBe('Logs')
    expect(p.paged[0]?.text).toContain('session created')
  })

  test('/sessions (and /resume) open the switcher with session.list rows', async () => {
    const p = makeCtx(async () => ({}))
    await dispatchSlash('/sessions', p.ctx)
    expect(p.switched).toHaveLength(1)
    expect(p.switched[0]).toEqual(FAKE_SESSIONS)
    const p2 = makeCtx(async () => ({}))
    await dispatchSlash('/resume', p2.ctx)
    expect(p2.switched).toHaveLength(1)
  })

  test('/help renders the gateway catalog', async () => {
    const p = makeCtx(async method =>
      method === 'commands.catalog' ? { pairs: [['/model', 'switch model']], canon: {} } : {}
    )
    await dispatchSlash('/help', p.ctx)
    expect(p.calls[0]?.method).toBe('commands.catalog')
    expect(p.system.join('\n')).toContain('/model — switch model')
  })
})

describe('dispatchSlash — server ladder', () => {
  test('unknown command → slash.exec; SHORT output shown as a system line', async () => {
    const p = makeCtx(async method => (method === 'slash.exec' ? { output: 'all good' } : {}))
    await dispatchSlash('/status', p.ctx)
    expect(p.calls[0]).toEqual({ method: 'slash.exec', params: { command: 'status', session_id: 'sid-1' } })
    expect(p.system).toContain('all good')
    expect(p.paged).toHaveLength(0)
  })

  test('LONG slash.exec output opens the pager (titled by command)', async () => {
    const longText = Array.from({ length: 6 }, (_, i) => `output line ${i}`).join('\n')
    const p = makeCtx(async method => (method === 'slash.exec' ? { output: longText } : {}))
    await dispatchSlash('/status', p.ctx)
    expect(p.paged).toHaveLength(1)
    expect(p.paged[0]?.title).toBe('Status')
    expect(p.paged[0]?.text).toContain('output line 5')
    expect(p.system).toHaveLength(0)
  })

  test('slash.exec rejects → command.dispatch; send result submits a user turn', async () => {
    const p = makeCtx(async method => {
      if (method === 'slash.exec') throw new Error('not a worker command')
      if (method === 'command.dispatch') return { type: 'send', message: 'run the thing' }
      return {}
    })
    await dispatchSlash('/dothing', p.ctx)
    expect(p.calls.map(c => c.method)).toEqual(['slash.exec', 'command.dispatch'])
    expect(p.submitted).toEqual(['run the thing'])
  })

  test('command.dispatch exec → system output', async () => {
    const p = makeCtx(async method => {
      if (method === 'slash.exec') throw new Error('reject')
      return { type: 'exec', output: 'done' }
    })
    await dispatchSlash('/whatever', p.ctx)
    expect(p.system).toContain('done')
  })
})
