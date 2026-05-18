import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { NotFoundPage } from "./NotFoundPage";

describe("NotFoundPage", () => {
  it("renders the signal-console recovery state", async () => {
    render(
      <MemoryRouter>
        <NotFoundPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("404")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /back to services/i })).toHaveAttribute(
      "href",
      "/services?environment=all",
    );
  });
});
