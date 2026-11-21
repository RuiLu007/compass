"use client";
import { useCallback, useEffect, useState } from "react";
import type { components } from "@/generated/api";
import { useDocumentSession } from "@/components/shell/session-boundary";

export type StarterCards = components["schemas"]["PromptStarterCards"];
export function useStarters(path: string | null, initialize?: (value: StarterCards) => void) {
  const api = useDocumentSession();
  const [cards, setCards] = useState<StarterCards | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const retry = useCallback(() => setAttempt((x) => x + 1), []);
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    void api
      .request<StarterCards>(path, "GET", undefined, {
        signal: controller.signal,
        retainOnNotFound: true,
      })
      .then((value) => {
        if (!controller.signal.aborted) {
          setCards(value);
          initialize?.(value);
          setFailed(false);
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setCards(null);
          setFailed(true);
        }
      });
    return () => controller.abort();
  }, [api, path, attempt, initialize]);
  return { cards, failed, retry };
}
