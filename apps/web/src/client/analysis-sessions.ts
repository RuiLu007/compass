import type { components } from "@/generated/api";

export type SessionListItem = components["schemas"]["SessionListItem"];
export const sessionsChanged = "compass-sessions-changed";

export function notifySessionsChanged(item?: SessionListItem | null) {
  window.dispatchEvent(new CustomEvent(sessionsChanged, { detail: item }));
}
