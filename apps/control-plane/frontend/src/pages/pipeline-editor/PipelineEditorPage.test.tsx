import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { PipelineEditorPage } from "./PipelineEditorPage";

vi.mock("../../features/pipelines/queries", () => ({
  usePipelineConnectorsQuery: () => ({
    data: {
      items: [
        {
          type: "handler",
          label: "Python Handler",
          category: "handler",
          description: "",
          fields: [],
        },
      ],
    },
    isPending: false,
    error: null,
  }),
  usePipelineCredentialsQuery: () => ({ data: { items: [] }, isPending: false, error: null }),
  usePipelineQuery: () => ({
    data: {
      id: "11111111-1111-1111-1111-111111111111",
      name: "Daily Sync",
      description: "",
      graph: { nodes: [], edges: [] },
      status: "draft",
      created_at: "2026-06-15T00:00:00Z",
      updated_at: "2026-06-15T00:00:00Z",
    },
    isPending: false,
    error: null,
  }),
  useUpdatePipelineMutation: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useValidatePipelineMutation: () => ({ isPending: false, mutateAsync: vi.fn() }),
}));

vi.mock("../../features/pipelines/components/PipelineEditor", () => ({
  PipelineEditor: () => <div data-testid="pipeline-editor-canvas">canvas</div>,
}));

describe("PipelineEditorPage", () => {
  it("renders builder controls without run or deploy actions", () => {
    const queryClient = new QueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/pipelines/11111111-1111-1111-1111-111111111111"]}>
          <Routes>
            <Route path="/pipelines/:pipelineId" element={<PipelineEditorPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByRole("heading", { name: "Daily Sync" })).toBeInTheDocument();
    expect(screen.getByTestId("pipeline-editor-canvas")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Validate" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Start|Stop|Run|Deploy/ })).not.toBeInTheDocument();
  });
});
