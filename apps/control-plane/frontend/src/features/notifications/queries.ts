import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createNotificationChannel,
  deleteNotificationChannel,
  listNotificationChannels,
  listNotificationServices,
  testNotificationChannel,
  updateNotificationChannel,
} from "../../lib/api/client";
import type { NotificationChannelPatchRequest, NotificationChannelUpsertRequest } from "../../lib/api/types";

const NOTIFICATION_CHANNELS_QUERY_KEY = ["notification-channels"];
const NOTIFICATION_SERVICES_QUERY_KEY = ["notification-services"];

export function useNotificationChannelsQuery() {
  return useQuery({
    queryKey: NOTIFICATION_CHANNELS_QUERY_KEY,
    queryFn: () => listNotificationChannels(),
  });
}

export function useNotificationServicesQuery() {
  return useQuery({
    queryKey: NOTIFICATION_SERVICES_QUERY_KEY,
    queryFn: () => listNotificationServices(),
  });
}

export function useCreateNotificationChannelMutation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: NotificationChannelUpsertRequest) => createNotificationChannel(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: NOTIFICATION_CHANNELS_QUERY_KEY });
    },
  });
}

export function useUpdateNotificationChannelMutation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ channelId, payload }: { channelId: string; payload: NotificationChannelPatchRequest }) =>
      updateNotificationChannel(channelId, payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: NOTIFICATION_CHANNELS_QUERY_KEY });
    },
  });
}

export function useDeleteNotificationChannelMutation() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (channelId: string) => deleteNotificationChannel(channelId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: NOTIFICATION_CHANNELS_QUERY_KEY });
    },
  });
}

export function useTestNotificationChannelMutation() {
  return useMutation({
    mutationFn: (channelId: string) => testNotificationChannel(channelId),
  });
}
