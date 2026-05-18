import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "../../components/ui/ToastProvider";
import type { NotificationChannel } from "../../lib/api/types";
import { SettingsNotificationsPage } from "./SettingsNotificationsPage";

const mockUseConsoleSessionQuery = vi.fn();
const mockUseNotificationChannelsQuery = vi.fn();
const mockUseNotificationServicesQuery = vi.fn();
const mockUseCreateNotificationChannelMutation = vi.fn();
const mockUseUpdateNotificationChannelMutation = vi.fn();
const mockUseDeleteNotificationChannelMutation = vi.fn();
const mockUseTestNotificationChannelMutation = vi.fn();

vi.mock("../../features/auth/queries", () => ({
  useConsoleSessionQuery: () => mockUseConsoleSessionQuery(),
}));

vi.mock("../../features/notifications/queries", () => ({
  useNotificationChannelsQuery: () => mockUseNotificationChannelsQuery(),
  useNotificationServicesQuery: () => mockUseNotificationServicesQuery(),
  useCreateNotificationChannelMutation: () => mockUseCreateNotificationChannelMutation(),
  useUpdateNotificationChannelMutation: () => mockUseUpdateNotificationChannelMutation(),
  useDeleteNotificationChannelMutation: () => mockUseDeleteNotificationChannelMutation(),
  useTestNotificationChannelMutation: () => mockUseTestNotificationChannelMutation(),
}));

function buildChannel(): NotificationChannel {
  return {
    id: "channel-1",
    name: "ops-feishu",
    provider: "feishu",
    webhook_url_masked: "https://example.com/...ishu",
    enabled: true,
    service_scopes: [{ name: "billing-worker", environment: "prod" }],
    event_types: ["task_failed"],
    missed_start_grace_seconds: 300,
    created_at: new Date("2026-05-15T08:00:00Z").toISOString(),
    updated_at: new Date("2026-05-15T08:00:00Z").toISOString(),
  };
}

function renderPage() {
  render(
    <ToastProvider>
      <MemoryRouter>
        <SettingsNotificationsPage />
      </MemoryRouter>
    </ToastProvider>,
  );
}

describe("SettingsNotificationsPage", () => {
  afterEach(() => {
    mockUseConsoleSessionQuery.mockReset();
    mockUseNotificationChannelsQuery.mockReset();
    mockUseNotificationServicesQuery.mockReset();
    mockUseCreateNotificationChannelMutation.mockReset();
    mockUseUpdateNotificationChannelMutation.mockReset();
    mockUseDeleteNotificationChannelMutation.mockReset();
    mockUseTestNotificationChannelMutation.mockReset();
  });

  it("keeps the saved webhook unchanged when editing other fields", async () => {
    const channel = buildChannel();
    const updateMutation = vi.fn().mockResolvedValue({
      ...channel,
      name: "ops-feishu-updated",
    } satisfies NotificationChannel);

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
    mockUseNotificationChannelsQuery.mockReturnValue({
      isPending: false,
      error: null,
      data: { items: [channel] },
    });
    mockUseNotificationServicesQuery.mockReturnValue({
      isPending: false,
      error: null,
      data: { items: [{ name: "billing-worker", environment: "prod" }] },
    });
    mockUseCreateNotificationChannelMutation.mockReturnValue({
      isPending: false,
      mutateAsync: vi.fn(),
    });
    mockUseUpdateNotificationChannelMutation.mockReturnValue({
      isPending: false,
      variables: undefined,
      mutateAsync: updateMutation,
    });
    mockUseDeleteNotificationChannelMutation.mockReturnValue({
      isPending: false,
      mutateAsync: vi.fn(),
    });
    mockUseTestNotificationChannelMutation.mockReturnValue({
      isPending: false,
      mutateAsync: vi.fn(),
    });

    renderPage();
    const user = userEvent.setup();

    expect(await screen.findByRole("heading", { name: "Notifications" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /ops-feishu/i }));
    expect(
      await screen.findByText(
        "Current webhook is stored securely as https://example.com/...ishu. Leave this field blank to keep it unchanged.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText("https://open.feishu.cn/open-apis/bot/v2/hook/..."),
    ).toHaveValue("");

    await user.clear(screen.getByLabelText("Name"));
    await user.type(screen.getByLabelText("Name"), "ops-feishu-updated");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(updateMutation).toHaveBeenCalledWith({
      channelId: "channel-1",
      payload: {
        name: "ops-feishu-updated",
        provider: "feishu",
        enabled: true,
        service_scopes: [{ name: "billing-worker", environment: "prod" }],
        event_types: ["task_failed"],
        missed_start_grace_seconds: 300,
      },
    });
  });
});
