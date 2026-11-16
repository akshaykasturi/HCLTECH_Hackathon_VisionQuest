import { ApiError } from "./api";
import type { HealthData } from "./types";

export type HealthState = "healthy" | "not_ready" | "unavailable" | "unknown";

/** Turn GET /health (or its failure) into the three component states. Never guesses "healthy". */
export function deriveHealth(data: HealthData | null, error: unknown):
  { api: HealthState; database: HealthState; warmup: HealthState } {
  if (!data) {
    // Network/timeout: the API cannot be reached; database and warm-up are then unknown, not "down".
    const unreachable = error instanceof ApiError && error.kind !== "aborted";
    return { api: unreachable ? "unavailable" : "unknown", database: "unknown", warmup: "unknown" };
  }
  return {
    api: data.api === "up" ? "healthy" : "unavailable",
    database: data.database === "up" ? "healthy" : data.database === "down" ? "unavailable" : "unknown",
    warmup: data.warmup === "ready" ? "healthy" : data.warmup === "warming" ? "not_ready"
      : data.warmup === "failed" ? "unavailable" : "unknown",
  };
}
