import type { Run } from "@/lib/types";

export function copyFailureDetail(
  run: Pick<Run, "copy_generation">,
): string {
  const copy = run.copy_generation;
  if (!copy) return "";
  return String(copy.last_error || copy.error_detail || copy.error_code || "").trim();
}

export function displayRunStatus(run: Pick<Run, "status" | "image_count" | "image_generation" | "copy_generation">): string {
  const raw = String(run.status || "unknown");
  if (["deleted", "deleting", "purge_failed", "failed", "canceled"].includes(raw)) {
    return raw;
  }
  const imageStatus = String(run.image_generation?.status || "");
  const copyDelivery = String(run.copy_generation?.delivery_status || "");
  const copyStatus = String(run.copy_generation?.status || "");
  const imageCount = Number(run.image_count || 0);
  if (imageStatus === "failed") return "failed";
  if (imageStatus === "running") return "generating";
  if (imageStatus === "completed" || (imageCount > 0 && ["queued", "running", "generating"].includes(raw))) {
    return "completed";
  }
  if (raw === "copy_completed" || copyDelivery === "delivered") return "copy_completed";
  if (copyStatus === "running" || copyStatus === "copy_queued" || raw === "copy_queued") return "copying";
  return raw;
}
