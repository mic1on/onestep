import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConnectorsPage } from "./ConnectorsPage";

const mockUseConnectorsQuery = vi.fn();
const mockCreate = vi.fn();
const mockUpdate = vi.fn();
const mockDelete = vi.fn();

vi.mock("../../features/connectors/queries", () => ({
  useConnectorsQuery: () => mockUseConnectorsQuery(),
  useCreateConnectorMutation: () => ({ isPending: false, mutateAsync: mockCreate }),
  useUpdateConnectorMutation: () => ({ isPending: false, mutateAsync: mockUpdate }),
  useDeleteConnectorMutation: () => ({ isPending: false, mutateAsync: mockDelete }),
}));

function renderPage() {
  const queryClient = new QueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ConnectorsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("ConnectorsPage", () => {
  beforeEach(() => {
    mockCreate.mockReset();
    mockUpdate.mockReset();
    mockDelete.mockReset();
  });

  it("renders the header and type accordions", () => {
    mockUseConnectorsQuery.mockReturnValue({
      data: { items: [], total: 0 },
      isPending: false,
      error: null,
    });

    renderPage();

    expect(screen.getByRole("heading", { name: "Connectors" })).toBeInTheDocument();
    expect(screen.getByText("Adapter library")).toBeInTheDocument();
    expect(screen.getAllByText("MySQL").length).toBeGreaterThan(0);
    expect(screen.getByText("Redis")).toBeInTheDocument();
  });

  it("renders existing connectors grouped by type", () => {
    mockUseConnectorsQuery.mockReturnValue({
      data: {
        items: [
          {
            id: "11111111-1111-1111-1111-111111111111",
            name: "prod-db",
            type: "mysql",
            config: {},
            secret: { dsn: "****" },
            created_at: "2026-06-17T00:00:00Z",
            updated_at: "2026-06-17T00:00:00Z",
          },
        ],
        total: 1,
      },
      isPending: false,
      error: null,
    });

    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /MySQL/ }));

    expect(screen.getByText("prod-db")).toBeInTheDocument();
  });

  it("creates MySQL connectors from split fields", async () => {
    const user = userEvent.setup();
    mockUseConnectorsQuery.mockReturnValue({
      data: { items: [], total: 0 },
      isPending: false,
      error: null,
    });
    mockCreate.mockResolvedValue({});

    renderPage();

    await user.click(screen.getAllByRole("button", { name: "New" })[0]);
    await user.type(screen.getByLabelText("Name"), "prod-db");
    await user.type(screen.getByLabelText("Host"), "10.0.0.1");
    await user.type(screen.getByLabelText("Database"), "orders");
    await user.type(screen.getByLabelText("Username"), "ops@example.com");
    await user.type(screen.getByLabelText("Password"), "pa@ss");
    await user.click(screen.getByRole("button", { name: "Create" }));

    expect(mockCreate).toHaveBeenCalledWith({
      name: "prod-db",
      type: "mysql",
      config: {
        host: "10.0.0.1",
        database: "orders",
        username: "ops@example.com",
      },
      secret: { password: "pa@ss" },
    });
  });

  it("creates MySQL connectors from an imported URI", async () => {
    const user = userEvent.setup();
    mockUseConnectorsQuery.mockReturnValue({
      data: { items: [], total: 0 },
      isPending: false,
      error: null,
    });
    mockCreate.mockResolvedValue({});

    renderPage();

    await user.click(screen.getAllByRole("button", { name: "New" })[0]);
    fireEvent.change(screen.getByLabelText("Connection URI"), {
      target: {
        value: "mysql://ops%40example.com:pa%40ss@10.0.0.1:3307/orders",
      },
    });
    await user.click(screen.getByRole("button", { name: "Import URI" }));

    expect(screen.getByLabelText("Connection URI")).toHaveValue("");
    expect(screen.getByLabelText("Name")).toHaveValue("orders");
    expect(screen.getByLabelText("Host")).toHaveValue("10.0.0.1");
    expect(screen.getByLabelText("Port")).toHaveValue("3307");
    expect(screen.getByLabelText("Database")).toHaveValue("orders");
    expect(screen.getByLabelText("Username")).toHaveValue("ops@example.com");
    expect(screen.getByLabelText("Password")).toHaveValue("pa@ss");

    await user.click(screen.getByRole("button", { name: "Create" }));

    expect(mockCreate).toHaveBeenCalledWith({
      name: "orders",
      type: "mysql",
      config: {
        host: "10.0.0.1",
        port: "3307",
        database: "orders",
        username: "ops@example.com",
      },
      secret: { password: "pa@ss" },
    });
  });
});
