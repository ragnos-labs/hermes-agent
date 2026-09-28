import { describe, expect, it } from "vitest";

import {
  buildCronJobEditPayload,
  buildCronJobPayload,
  cronJobHasExecutionContent,
  cronJobFormFromJob,
  splitCronList,
  type CronJobFormState,
} from "./cron-job";
import type { CronJob } from "./api";

function form(overrides: Partial<CronJobFormState> = {}): CronJobFormState {
  return {
    name: "",
    prompt: "prompt",
    schedule: "every 1h",
    deliver: "local",
    skills: [],
    provider: "",
    model: "",
    base_url: "",
    script: "",
    no_agent: false,
    context_from: "",
    continuity: false,
    enabled_toolsets: [],
    workdir: "",
    ...overrides,
  };
}

describe("splitCronList", () => {
  it("normalizes comma and newline separated cron list fields", () => {
    expect(splitCronList(" web, terminal\nfile ,, ")).toEqual([
      "web",
      "terminal",
      "file",
    ]);
  });
});

describe("buildCronJobPayload", () => {
  // The shown name of an unnamed job is the first 50 characters of its prompt.
  const derived = "sk-live-7f3a9c SECRET_PROMPT_MARKER rotate the va";

  it("sends the name on create", () => {
    expect(buildCronJobPayload(form({ name: " nightly " })).name).toBe(
      "nightly",
    );
  });

  it("omits an unedited name on edit so a stale name is not re-sent", () => {
    const payload = buildCronJobPayload(
      form({ name: derived, prompt: "edited prompt" }),
      { shownName: derived },
    );
    expect("name" in payload).toBe(false);
    expect(payload.prompt).toBe("edited prompt");
  });

  it("omits a name that differs from the shown one only at the edges", () => {
    // The form is pre-filled with the raw shown name. JavaScript trim removes
    // U+FEFF, which Python str.strip keeps.
    const shown = `\ufeff${derived}`;
    const options = { shownName: shown };
    const unchanged = buildCronJobPayload(form({ name: shown }), options);
    const trimmed = buildCronJobPayload(form({ name: derived }), options);
    expect("name" in unchanged).toBe(false);
    expect("name" in trimmed).toBe(false);
  });

  it("sends a real rename on edit, including a case-only one", () => {
    const renamed = buildCronJobPayload(form({ name: "vault rotation" }), {
      shownName: derived,
    });
    const recased = buildCronJobPayload(form({ name: "Vault Rotation" }), {
      shownName: "vault rotation",
    });
    expect(renamed.name).toBe("vault rotation");
    expect(recased.name).toBe("Vault Rotation");
  });

  it("sends a cleared name on edit so the server can clear the marker", () => {
    const cleared = buildCronJobPayload(form({ name: "" }), {
      shownName: "vault rotation",
    });
    expect(cleared.name).toBe("");
  });

  it("normalizes list fields and base URLs", () => {
    const payload = buildCronJobPayload(
      form({
        base_url: "https://example.invalid/v1/",
        enabled_toolsets: ["web", ""],
        context_from: "upstream-a\nupstream-b",
      }),
    );

    expect(payload).toMatchObject({
      base_url: "https://example.invalid/v1",
      context_from: ["upstream-a", "upstream-b"],
      enabled_toolsets: ["web"],
    });
  });

  it("stores continuity as the reserved self entry", () => {
    const payload = buildCronJobPayload(
      form({ continuity: true, context_from: "upstream-a" }),
    );

    expect(payload.context_from).toEqual(["upstream-a", "self"]);
  });

  it("continuity off strips any hand-typed self entry", () => {
    const payload = buildCronJobPayload(
      form({ continuity: false, context_from: "SELF\nupstream-a" }),
    );

    expect(payload.context_from).toEqual(["upstream-a"]);
  });

  it("keeps clear operations explicit for update payloads", () => {
    const payload = buildCronJobPayload(form({ schedule: "every 2h" }));

    expect(payload).toMatchObject({
      schedule: "every 2h",
      provider: null,
      model: null,
      base_url: null,
      script: null,
      no_agent: false,
      context_from: null,
      enabled_toolsets: null,
      workdir: null,
    });
  });
});

describe("cronJobHasExecutionContent", () => {
  it("treats a script as execution content for agent-backed cron jobs", () => {
    const payload = buildCronJobPayload(
      form({ prompt: "", skills: [], script: "collect-status.py" }),
    );

    expect(cronJobHasExecutionContent(payload)).toBe(true);
  });

  it("rejects payloads with no prompt, skills, or script", () => {
    const payload = buildCronJobPayload(form({ prompt: "", skills: [], script: "" }));

    expect(cronJobHasExecutionContent(payload)).toBe(false);
  });
});

describe("cronJobFormFromJob", () => {
  it("preserves schedule fallback and editable list fields", () => {
    const job: CronJob = {
      id: "abc",
      enabled: true,
      schedule_display: "every 1h",
      context_from: ["upstream-a", "upstream-b"],
      enabled_toolsets: ["web"],
    };

    expect(cronJobFormFromJob(job)).toMatchObject({
      schedule: "every 1h",
      context_from: "upstream-a\nupstream-b",
      continuity: false,
      enabled_toolsets: ["web"],
    });
  });

  it("splits the stored self entry into the continuity toggle", () => {
    const job: CronJob = {
      id: "abc",
      enabled: true,
      schedule_display: "every 1h",
      context_from: ["self", "upstream-a"],
    };

    expect(cronJobFormFromJob(job)).toMatchObject({
      context_from: "upstream-a",
      continuity: true,
    });
  });

  it("prefers one-shot run_at over the human display string", () => {
    const job: CronJob = {
      id: "once-job",
      enabled: true,
      schedule: {
        kind: "once",
        run_at: "2026-02-03T14:00:00+08:00",
      },
      schedule_display: "once at 2026-02-03 14:00",
    };

    expect(cronJobFormFromJob(job)).toMatchObject({
      schedule: "2026-02-03T14:00:00+08:00",
    });
  });
});

describe("buildCronJobEditPayload", () => {
  // The server shows an unnamed job under the start of its prompt.
  const unnamedJob: CronJob = {
    id: "abc123def456",
    enabled: true,
    name: "sk-live-7f3a9c rotate the vault token",
    prompt: "sk-live-7f3a9c rotate the vault token and post it",
    schedule_display: "every 1h",
  };

  it("omits the name the edit form was pre-filled with", () => {
    const edited = { ...cronJobFormFromJob(unnamedJob), prompt: "harmless" };
    const payload = buildCronJobEditPayload(unnamedJob, edited);

    expect(payload).not.toHaveProperty("name");
    expect(payload.prompt).toBe("harmless");
  });

  it("omits a padded stored name the form showed", () => {
    const job: CronJob = { ...unnamedJob, name: "  vault  " };
    const edited = { ...cronJobFormFromJob(job), name: "vault" };

    expect(buildCronJobEditPayload(job, edited)).not.toHaveProperty("name");
  });

  it("sends a rename and a cleared name", () => {
    const form = cronJobFormFromJob(unnamedJob);

    expect(
      buildCronJobEditPayload(unnamedJob, { ...form, name: "Vault rotation" })
        .name,
    ).toBe("Vault rotation");
    expect(buildCronJobEditPayload(unnamedJob, { ...form, name: "" }).name).toBe(
      "",
    );
  });
});
