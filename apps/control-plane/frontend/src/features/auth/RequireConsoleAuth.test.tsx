import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SessionExpiredError } from "../../lib/api/client";
import { RequireConsoleAuth } from "./RequireConsoleAuth";

const mockUseConsoleSessionQuery = vi.fn();

vi.mock("./queries", () => ({
  useConsoleSessionQuery: () => mockUseConsoleSessionQuery(),
}));

function renderGuard(initialEntry = "/services?environment=prod") {
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/" element={<RequireConsoleAuth />}>
          <Route path="services" element={<div>Protected page</div>} />
        </Route>
        <Route path="/login" element={<div>Login page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("RequireConsoleAuth", () => {
  afterEach(() => {
    mockUseConsoleSessionQuery.mockReset();
  });

  it("redirects unauthenticated users to login with the current path as next", async () => {
    mockUseConsoleSessionQuery.mockReturnValue({
      isPending: false,
      error: null,
      data: {
        auth_configured: true,
        bootstrap_required: false,
        authenticated: false,
        username: null,
        role: null,
        roles: [],
      },
    });

    renderGuard();

    expect(await screen.findByText("Login page")).toBeInTheDocument();
  });

  it("renders protected content for authenticated users", async () => {
    mockUseConsoleSessionQuery.mockReturnValue({
      isPending: false,
      error: null,
      data: {
        auth_configured: true,
        bootstrap_required: false,
        authenticated: true,
        username: "admin",
        role: "admin",
        roles: ["admin"],
      },
    });

    renderGuard();

    expect(await screen.findByText("Protected page")).toBeInTheDocument();
  });

  it("renders a non-crashing auth panel when the session query expires", async () => {
    mockUseConsoleSessionQuery.mockReturnValue({
      isPending: false,
      error: new SessionExpiredError(),
      data: undefined,
    });

    renderGuard();

    expect(await screen.findByText(/sign in/i)).toBeInTheDocument();
  });

  it("does not render protected content when local admin bootstrap is required", async () => {
    mockUseConsoleSessionQuery.mockReturnValue({
      isPending: false,
      error: null,
      data: {
        auth_configured: false,
        bootstrap_required: true,
        authenticated: false,
        username: null,
        role: null,
        roles: [],
      },
    });

    renderGuard();

    expect(
      await screen.findByText("Local admin bootstrap is required before console access."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Protected page")).not.toBeInTheDocument();
  });
});
