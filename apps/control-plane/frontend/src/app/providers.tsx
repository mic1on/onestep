import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router-dom";

import { ToastProvider } from "../components/ui/ToastProvider";
import { useCommandStream } from "../features/commands/useCommandStream";
import { router } from "./router";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

export function AppProviders() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <CommandStreamBridge />
        <RouterProvider router={router} />
      </ToastProvider>
    </QueryClientProvider>
  );
}

function CommandStreamBridge() {
  useCommandStream();
  return null;
}
