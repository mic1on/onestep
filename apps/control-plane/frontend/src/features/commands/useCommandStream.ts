import { useEffect, useEffectEvent, useRef, useSyncExternalStore } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { useToast } from "../../components/ui/ToastProvider";
import { buildApiUrl } from "../../lib/api/client";
import type {
  UiStreamChannel,
  UiStreamConnectionPhase,
  UiStreamConnectionState,
  UiStreamEvent,
} from "../../lib/api/types";
import { useConsoleSessionQuery } from "../auth/queries";
import {
  COMMAND_QUERY_STALE_AFTER_MS,
  invalidateCommandStreamQueries,
  invalidateSessionStreamQueries,
} from "./queries";

const INVALIDATION_DEBOUNCE_MS = 150;
const STREAM_STALE_AFTER_MS = COMMAND_QUERY_STALE_AFTER_MS;

const INITIAL_STREAM_STATE: UiStreamConnectionState = {
  phase: "disabled",
  last_connected_at: null,
  last_event_at: null,
  last_error_at: null,
};

let streamState = INITIAL_STREAM_STATE;
const streamStateListeners = new Set<() => void>();

function subscribeToStreamState(listener: () => void) {
  streamStateListeners.add(listener);
  return () => {
    streamStateListeners.delete(listener);
  };
}

function getStreamState() {
  return streamState;
}

function setStreamState(
  next:
    | UiStreamConnectionState
    | ((current: UiStreamConnectionState) => UiStreamConnectionState),
) {
  const resolved = typeof next === "function" ? next(streamState) : next;
  if (
    resolved.phase === streamState.phase &&
    resolved.last_connected_at === streamState.last_connected_at &&
    resolved.last_event_at === streamState.last_event_at &&
    resolved.last_error_at === streamState.last_error_at
  ) {
    return;
  }

  streamState = resolved;
  for (const listener of streamStateListeners) {
    listener();
  }
}

export function useCommandStreamStatus() {
  return useSyncExternalStore(subscribeToStreamState, getStreamState, getStreamState);
}

export function useCommandStream() {
  const queryClient = useQueryClient();
  const { t } = useTranslation();
  const { pushToast } = useToast();
  const sessionQuery = useConsoleSessionQuery();
  const streamStatus = useCommandStreamStatus();
  const pendingChannelsRef = useRef<Set<UiStreamChannel>>(new Set());
  const flushTimerRef = useRef<number | null>(null);
  const staleTimerRef = useRef<number | null>(null);
  const previousPhaseRef = useRef<UiStreamConnectionPhase>(INITIAL_STREAM_STATE.phase);
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

  const clearStaleTimer = useEffectEvent(() => {
    if (staleTimerRef.current !== null) {
      window.clearTimeout(staleTimerRef.current);
      staleTimerRef.current = null;
    }
  });

  const scheduleStaleTransition = useEffectEvent((phase: UiStreamConnectionPhase) => {
    clearStaleTimer();
    staleTimerRef.current = window.setTimeout(() => {
      setStreamState((current) =>
        current.phase === phase
          ? {
              ...current,
              phase: "stale",
            }
          : current,
      );
    }, STREAM_STALE_AFTER_MS);
  });

  useEffect(() => {
    if (!canConnect) {
      clearStaleTimer();
      setStreamState(INITIAL_STREAM_STATE);
      return undefined;
    }

    setStreamState((current) => ({
      ...current,
      phase: current.last_connected_at === null ? "connecting" : current.phase,
    }));

    const eventSource = new EventSource(buildApiUrl("/api/v1/ui/stream").toString(), {
      withCredentials: true,
    });

    eventSource.onopen = () => {
      clearStaleTimer();
      setStreamState((current) => ({
        ...current,
        phase: "connected",
        last_connected_at: Date.now(),
      }));
    };

    eventSource.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as UiStreamEvent;
        setStreamState((current) => ({
          ...current,
          phase: "connected",
          last_connected_at: current.last_connected_at ?? Date.now(),
          last_event_at: Date.now(),
        }));
        if (payload.channel === "commands" || payload.channel === "sessions") {
          scheduleInvalidation(payload.channel);
        }
      } catch {
        // Ignore malformed SSE frames and rely on the polling fallback.
      }
    };

    eventSource.onerror = () => {
      const nextPhase = getStreamState().last_connected_at === null ? "error" : "reconnecting";
      setStreamState((current) => ({
        ...current,
        phase: nextPhase,
        last_error_at: Date.now(),
      }));
      scheduleStaleTransition(nextPhase);
    };

    return () => {
      clearStaleTimer();
      if (flushTimerRef.current !== null) {
        window.clearTimeout(flushTimerRef.current);
        flushTimerRef.current = null;
      }
      pendingChannelsRef.current.clear();
      eventSource.close();
    };
  }, [canConnect]);

  useEffect(() => {
    if (streamStatus.phase === previousPhaseRef.current) {
      return;
    }

    const previousPhase = previousPhaseRef.current;
    previousPhaseRef.current = streamStatus.phase;

    if (streamStatus.phase === "reconnecting" && previousPhase === "connected") {
      pushToast({
        tone: "warning",
        message: t("controlPlaneStatus.toast.reconnecting"),
      });
      return;
    }

    if (streamStatus.phase === "stale" && (previousPhase === "reconnecting" || previousPhase === "error")) {
      pushToast({
        tone: "warning",
        message: t("controlPlaneStatus.toast.stale"),
      });
      return;
    }

    if (
      streamStatus.phase === "connected" &&
      (previousPhase === "reconnecting" || previousPhase === "stale" || previousPhase === "error")
    ) {
      pushToast({
        tone: "info",
        message: t("controlPlaneStatus.toast.recovered"),
      });
    }
  }, [pushToast, streamStatus.phase, t]);
}
