import type { FeedbackEntry } from "../../lib/types";

/** Feedback is append-only: the newest entry is the current verdict. Entries without a time keep list order. */
export function latestFeedback(feedback: FeedbackEntry[]): FeedbackEntry | null {
  if (feedback.length === 0) return null;
  return [...feedback].sort((a, b) => (b.at ?? "").localeCompare(a.at ?? ""))[0];
}
