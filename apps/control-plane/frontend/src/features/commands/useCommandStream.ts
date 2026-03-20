import { useEffect, useEffectEvent, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { useConsoleSessionQuery } from "../auth/queries";
import { buildApiUrl } from "../../lib/api/client";
import type { UiStreamChannel, UiStreamEvent } from "../../lib/api/types";
import { invalidateCommandStreamQueries, invalidateSessionStreamQueries } from "./queries";

const INVALIDATION_DEBOUNCE_MS = 150;

export function useCommandStream() {
  const queryClient = useQueryClient();
  const sessionQuery = useConsoleSessionQuery();
  const pendingChannelsRef = useRef<Set<UiStreamChannel>>(new Set());
  const flushTimerRef = useRef<number | null>(null);
  const authConfigured = sessionQuery.data?.auth_configured ?? false;
  const isAuthenticated = sessionQuery.data?.authenticated ?? false;
  const canConnect = !sessionQuery.isPending && !sessionQuery.error && (!authConfigured || isAuthenticated);

  const flushPendingInvalidations = useEffectEvent(() => {
    const channels = Array.from(pendingChannelsRef.current);
    pendingChannelsRef.current.clear();
    flushTimerRef.current = null;
    if (channels.length === 0) {
      return;
    }

    void Promise.all(
      channels.map((channel) =>
        channel === "commands"
          ? invalidateCommandStreamQueries(queryClient)
          : invalidateSessionStreamQueries(queryClient),
      ),
    );
  });

  const scheduleInvalidation = useEffectEvent((channel: UiStreamChannel) => {
    pendingChannelsRef.current.add(channel);
    if (flushTimerRef.current !== null) {
      return;
    }
    flushTimerRef.current = window.setTimeout(() => {
      flushPendingInvalidations();
    }, INVALIDATION_DEBOUNCE_MS);
  });

  useEffect(() => {
    if (!canConnect) {
      return undefined;
    }

    const eventSource = new EventSource(buildApiUrl("/api/v1/ui/stream").toString(), {
      withCredentials: true,
    });

    eventSource.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as UiStreamEvent;
        if (payload.channel === "commands" || payload.channel === "sessions") {
          scheduleInvalidation(payload.channel);
        }
      } catch {
        // Ignore malformed SSE frames and rely on the polling fallback.
      }
    };

    eventSource.onerror = () => {
      // EventSource handles retries internally; queries keep polling as fallback.
    };

    return () => {
      if (flushTimerRef.current !== null) {
        window.clearTimeout(flushTimerRef.current);
        flushTimerRef.current = null;
      }
      pendingChannelsRef.current.clear();
      eventSource.close();
    };
  }, [canConnect]);
}
