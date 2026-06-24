import { useQuery } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useTaskDetailQuery } from "./queries";

vi.mock("@tanstack/react-query", () => ({
  useQuery: vi.fn(),
}));

vi.mock("../../lib/api/client", () => ({
  getTaskDetail: vi.fn(),
}));

describe("useTaskDetailQuery", () => {
  afterEach(() => {
    vi.mocked(useQuery).mockReset();
  });

  it("polls task detail like the other live detail pages", () => {
    vi.mocked(useQuery).mockReturnValue({} as ReturnType<typeof useQuery>);

    useTaskDetailQuery("demo-service", "follow_record_sync", "prod", 60);

    expect(useQuery).toHaveBeenCalledWith(
      expect.objectContaining({
        queryKey: ["task-detail", "demo-service", "follow_record_sync", "prod", 60],
        refetchInterval: 5_000,
      }),
    );
  });
});
