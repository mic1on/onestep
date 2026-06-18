import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  createConnector,
  deleteConnector,
  listConnectors,
  updateConnector,
} from "../../lib/api/client";
import type {
  ConnectorCreateRequest,
  ConnectorType,
  ConnectorUpdateRequest,
} from "../../lib/api/types";

export const CONNECTORS_QUERY_KEY = ["connectors"];

export function useConnectorsQuery(typeFilter?: ConnectorType) {
  return useQuery({
    queryKey: typeFilter ? ["connectors", typeFilter] : CONNECTORS_QUERY_KEY,
    queryFn: () => listConnectors(typeFilter),
  });
}

export function useCreateConnectorMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: ConnectorCreateRequest) => createConnector(payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: CONNECTORS_QUERY_KEY }),
  });
}

export function useUpdateConnectorMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: ConnectorUpdateRequest }) =>
      updateConnector(id, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: CONNECTORS_QUERY_KEY }),
  });
}

export function useDeleteConnectorMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteConnector(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: CONNECTORS_QUERY_KEY }),
  });
}
