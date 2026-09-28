// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { CronJob } from "@/lib/api";

const apiMocks = vi.hoisted(() => ({
  getCronJobs: vi.fn(),
  getCronDeliveryTargets: vi.fn(),
  getProfiles: vi.fn(),
  getSkills: vi.fn(),
  getToolsets: vi.fn(),
  getModelOptions: vi.fn(),
  updateCronJob: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMocks }));
vi.mock("@/plugins", () => ({ PluginSlot: () => null }));
vi.mock("@/components/AutomationBlueprints", () => ({
  AutomationBlueprints: () => null,
}));
vi.mock("@/contexts/usePageHeader", () => ({
  usePageHeader: () => ({ setEnd: vi.fn(), setTitle: vi.fn() }),
}));

import CronPage from "./CronPage";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

// The name the server shows for a job whose stored name is blank: the first
// 50 characters of its prompt.
const PROMPT = "rotate the vault token and post the new value";

function unnamedJob(): CronJob {
  return {
    id: "job-1",
    name: PROMPT,
    prompt: PROMPT,
    schedule: { kind: "interval", expr: "every 1h" },
    schedule_display: "every 1h",
    enabled: true,
    state: "scheduled",
    deliver: "local",
    skills: [],
  } as unknown as CronJob;
}

let container: HTMLDivElement;
let root: Root;

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function setFieldValue(
  element: HTMLInputElement | HTMLTextAreaElement,
  value: string,
) {
  const proto =
    element instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")?.set?.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
}

function byId<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) throw new Error(`missing #${id}`);
  return element as T;
}

function buttonByText(text: string): HTMLButtonElement {
  const button = [...document.querySelectorAll("button")].find(
    (candidate) => candidate.textContent?.trim() === text,
  );
  if (!button) throw new Error(`missing button ${text}`);
  return button;
}

async function openEditorAndSave(edit: () => void) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  await act(async () => root.render(<CronPage />));
  await flush();

  const editButton = document.querySelector<HTMLButtonElement>(
    'button[aria-label="Edit job"]',
  );
  if (!editButton) throw new Error("missing edit button");
  await act(async () => editButton.click());
  expect(byId<HTMLInputElement>("edit-cron-name").value).toBe(PROMPT);

  await act(async () => edit());
  await act(async () => buttonByText("Save changes").click());
  await flush();

  expect(apiMocks.updateCronJob).toHaveBeenCalledTimes(1);
  const [jobId, payload] = apiMocks.updateCronJob.mock.calls[0];
  expect(jobId).toBe("job-1");
  return payload as Record<string, unknown>;
}

beforeEach(() => {
  apiMocks.getCronJobs.mockResolvedValue([unnamedJob()]);
  apiMocks.getCronDeliveryTargets.mockResolvedValue({
    targets: [
      { id: "local", name: "Local", home_target_set: true, home_env_var: null },
    ],
  });
  apiMocks.getProfiles.mockResolvedValue({ profiles: [] });
  apiMocks.getSkills.mockResolvedValue([]);
  apiMocks.getToolsets.mockResolvedValue([]);
  apiMocks.getModelOptions.mockResolvedValue(null);
  apiMocks.updateCronJob.mockResolvedValue(unnamedJob());
});

afterEach(async () => {
  await act(async () => root?.unmount());
  container?.remove();
  vi.clearAllMocks();
});

describe("CronPage edit", () => {
  it("leaves the shown name out of a prompt edit", async () => {
    const payload = await openEditorAndSave(() =>
      setFieldValue(
        byId<HTMLTextAreaElement>("edit-cron-prompt"),
        "harmless replacement prompt",
      ),
    );
    expect(payload.prompt).toBe("harmless replacement prompt");
    expect(payload).not.toHaveProperty("name");
  });

  it("sends a name the user typed", async () => {
    const payload = await openEditorAndSave(() =>
      setFieldValue(byId<HTMLInputElement>("edit-cron-name"), "vault rotation"),
    );
    expect(payload.name).toBe("vault rotation");
  });
});
