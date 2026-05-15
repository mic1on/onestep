import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LoginPage } from "./LoginPage";

const mockUseConsoleSessionQuery = vi.fn();
const mockLoginConsole = vi.fn();

vi.mock("../../features/auth/queries", () => ({
  consoleSessionQueryKey: ["console-session"],
  useConsoleSessionQuery: () => mockUseConsoleSessionQuery(),
}));

vi.mock("../../lib/api/client", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api/client")>("../../lib/api/client");
  return {
    ...actual,
    loginConsole: (...args: Parameters<typeof actual.loginConsole>) => mockLoginConsole(...args),
  };
});

function renderLoginPage(initialEntry = "/login?next=%2Fservices%3Fenvironment%3Dprod") {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/services" element={<div>Services page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("LoginPage", () => {
  afterEach(() => {
    mockUseConsoleSessionQuery.mockReset();
    mockLoginConsole.mockReset();
  });

  it("redirects to the next path after a successful login", async () => {
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
    mockLoginConsole.mockResolvedValue({
      auth_configured: true,
      bootstrap_required: false,
      authenticated: true,
      username: "operator",
      role: "operator",
      roles: ["operator"],
    });

    renderLoginPage();
    const user = userEvent.setup();

    await user.type(screen.getByLabelText(/username/i), "operator");
    await user.type(screen.getByLabelText(/password/i), "secret");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByText("Services page")).toBeInTheDocument();
    expect(mockLoginConsole).toHaveBeenCalledWith("operator", "secret");
  });

  it("shows an error when login fails", async () => {
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
    mockLoginConsole.mockRejectedValue(new Error("invalid username or password"));

    renderLoginPage();
    const user = userEvent.setup();

    await user.type(screen.getByLabelText(/username/i), "viewer");
    await user.type(screen.getByLabelText(/password/i), "bad");
    await user.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByText("invalid username or password")).toBeInTheDocument();
  });

  it("shows bootstrap guidance instead of redirecting when local admin setup is required", async () => {
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

    renderLoginPage("/login");

    expect(
      await screen.findByText("Local admin bootstrap is required before console login."),
    ).toBeInTheDocument();
  });
});
