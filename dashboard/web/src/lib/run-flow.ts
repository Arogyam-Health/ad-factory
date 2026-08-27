import type { Run } from "@/lib/types";

const REFERENCE_FLOWS = new Set(["reference", "reference_image"]);

export function isReferenceRun(run: Pick<Run, "flow_type" | "display_batch" | "flow">): boolean {
  const flow = String(run.flow_type || run.flow || "").toLowerCase();
  if (REFERENCE_FLOWS.has(flow)) return true;
  if (flow === "structured") return false;
  return /^ref[_-]?v/i.test(String(run.display_batch || ""));
}

export function filterRunsByFlow(runs: Run[], flow: "structured" | "reference"): Run[] {
  return runs.filter((run) => isReferenceRun(run) === (flow === "reference"));
}

export function mergeRunLists(primary: Run[], extra: Run[] = []): Run[] {
  const byId = new Map<string, Run>();
  for (const run of [...extra, ...primary]) {
    const id = run.run_id || "";
    if (!id) continue;
    byId.set(id, { ...byId.get(id), ...run });
  }
  return [...byId.values()].sort((a, b) => Number(b.created_at || 0) - Number(a.created_at || 0));
}

export function overlayLocalRunStats(server: Run[], local: Run[] = []): Run[] {
  if (!local.length) return server;
  const byId = new Map(local.map((run) => [run.run_id || "", run]));
  return server.map((run) => {
    const extra = byId.get(run.run_id || "");
    if (!extra) return run;
    return {
      ...run,
      display_batch: run.display_batch || extra.display_batch,
      prompt_count: Number(extra.prompt_count || 0) || run.prompt_count,
      image_count: Number(extra.image_count || 0) || run.image_count,
    };
  });
}

export function isActiveRun(run: Run): boolean {
  const status = String(run.status || "");
  const image = String(run.image_generation?.status || "");
  const copy = String(run.copy_generation?.status || "");
  return (
    ["queued", "running", "copying", "generating", "copy_queued"].includes(status)
    || ["queued", "running"].includes(image)
    || ["queued", "running", "copy_queued"].includes(copy)
  );
}
