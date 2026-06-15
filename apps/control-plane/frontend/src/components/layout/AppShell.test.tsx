import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "./AppShell";

const mockUseConsoleSessionQuery = vi.fn();

vi.mock("../../features/auth/queries", () => ({
  useConsoleSessionQuery: () => mockUseConsoleSessionQuery(),
}));

function renderShell() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/services?environment=all"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/services" element={<div>Services page</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AppShell", () => {
  afterEach(() => {
    mockUseConsoleSessionQuery.mockReset();
  });

  it("hides the notifications navigation link for viewer accounts", async () => {
    mockUseConsoleSessionQuery.mockReturnValue({
      isPending: false,
      error: null,
      data: {
        auth_configured: true,
        bootstrap_required: false,
        authenticated: true,
        username: "viewer",
        role: "viewer",
        roles: ["viewer"],
      },
    });

    renderShell();

    expect(await screen.findByText("Services page")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Notifications" })).not.toBeInTheDocument();
  });

  it("shows the notifications navigation link for operator accounts", async () => {
    mockUseConsoleSessionQuery.mockReturnValue({
      isPending: false,
      error: null,
      data: {
        auth_configured: true,
        bootstrap_required: false,
        authenticated: true,
        username: "operator",
        role: "operator",
        roles: ["operator"],
      },
    });

    renderShell();

    expect(await screen.findByText("Services page")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Pipelines" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Notifications" })).toBeInTheDocument();
  });
});
