import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { VibehubUpload } from "./VibehubUpload";

function UploadHarness() {
  const [file, setFile] = useState<File | null>(null);

  return (
    <VibehubUpload
      actionLabel="Upload"
      badgeLabel="ZIP"
      description="Upload a workflow package zip."
      emptyText="Click or drop to upload"
      label="Package file"
      onChange={setFile}
      selectedText="Selected package"
      value={file}
    />
  );
}

describe("VibehubUpload", () => {
  it("shows the selected file after upload", async () => {
    const user = userEvent.setup();
    const file = new File(["workflow"], "workflow.zip", {
      type: "application/zip",
    });

    render(<UploadHarness />);

    expect(screen.getByText("Click or drop to upload")).toBeInTheDocument();

    const input = document.querySelector<HTMLInputElement>(".vibehub-upload-input");
    expect(input).not.toBeNull();

    await user.upload(input as HTMLInputElement, file);

    expect(screen.getByText("workflow.zip")).toBeInTheDocument();
    expect(screen.getByText(/Selected package/)).toBeInTheDocument();
  });
});
