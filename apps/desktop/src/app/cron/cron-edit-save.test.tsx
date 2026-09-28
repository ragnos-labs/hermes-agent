import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type * as HermesApi from '@/hermes'
import type { CronJob } from '@/hermes'
import { $cronJobs } from '@/store/cron'
import { stubMenuDomApis, stubResizeObserver } from '@/test/jsdom'

import { CronView } from './index'

// The edit dialog pre-fills the name the server shows. For a job whose stored
// name is blank, that name is the first 50 characters of its prompt, so the
// save must leave it out unless the user changed it (cronJobEditUpdates).
// These tests cover the CronView call site, not only the helper.

const hermes = vi.hoisted(() => ({
  getAutomationBlueprints: vi.fn(),
  getCronDeliveryTargets: vi.fn(),
  getCronJobRuns: vi.fn(),
  getCronJobs: vi.fn(),
  updateCronJob: vi.fn()
}))

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<typeof HermesApi>()),
  ...hermes
}))
vi.mock('@/lib/model-options', () => ({
  requestModelOptions: vi.fn(() => Promise.resolve({ providers: [] }))
}))

const PROMPT = 'rotate the vault token and post the new value'

function unnamedJob(): CronJob {
  return {
    deliver: 'local',
    enabled: true,
    id: 'job-1',
    name: PROMPT,
    prompt: PROMPT,
    schedule: { expr: '0 9 * * *', kind: 'cron' },
    schedule_display: '0 9 * * *',
    state: 'scheduled'
  } as unknown as CronJob
}

beforeEach(() => {
  stubResizeObserver()
  stubMenuDomApis()
  $cronJobs.set([])
  hermes.getAutomationBlueprints.mockResolvedValue({ blueprints: [] })
  hermes.getCronDeliveryTargets.mockResolvedValue([])
  hermes.getCronJobRuns.mockResolvedValue([])
  hermes.getCronJobs.mockResolvedValue([unnamedJob()])
  hermes.updateCronJob.mockResolvedValue(unnamedJob())
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function openEditorAndSave(edit: () => void): Promise<Record<string, unknown>> {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  render(
    <QueryClientProvider client={client}>
      <CronView onClose={() => {}} />
    </QueryClientProvider>
  )

  const [trigger] = await screen.findAllByRole('button', { name: 'Manage' })

  fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false, pointerType: 'mouse' })
  fireEvent.click(await screen.findByRole('menuitem', { name: 'Edit cron' }))

  const nameField = (await screen.findByLabelText(/Name/)) as HTMLInputElement

  await waitFor(() => expect(nameField.value).toBe(PROMPT))

  edit()
  fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))

  await waitFor(() => expect(hermes.updateCronJob).toHaveBeenCalledTimes(1))
  const [jobId, updates] = hermes.updateCronJob.mock.calls[0]

  expect(jobId).toBe('job-1')

  return updates as Record<string, unknown>
}

describe('CronView edit save', () => {
  it('leaves the shown name out of a prompt edit', async () => {
    const updates = await openEditorAndSave(() =>
      fireEvent.change(screen.getByLabelText(/Prompt/), { target: { value: 'harmless replacement prompt' } })
    )

    expect(updates.prompt).toBe('harmless replacement prompt')
    expect(updates).not.toHaveProperty('name')
  })

  it('sends a name the user typed', async () => {
    const updates = await openEditorAndSave(() =>
      fireEvent.change(screen.getByLabelText(/Name/), { target: { value: 'vault rotation' } })
    )

    expect(updates.name).toBe('vault rotation')
  })
})
