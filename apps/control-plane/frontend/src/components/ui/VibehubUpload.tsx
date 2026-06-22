import {
  useId,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
} from "react";

type VibehubUploadProps = {
  label: string;
  value: File | null;
  onChange: (file: File | null) => void;
  accept?: string;
  actionLabel?: string;
  badgeLabel?: string;
  className?: string;
  description?: string;
  disabled?: boolean;
  emptyText: string;
  inputName?: string;
  selectedText?: string;
};

export function VibehubUpload({
  accept,
  actionLabel,
  badgeLabel,
  className,
  description,
  disabled = false,
  emptyText,
  inputName,
  label,
  onChange,
  selectedText,
  value,
}: VibehubUploadProps) {
  const generatedId = useId();
  const inputId = `vibehub-upload-${generatedId}`;
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const hasFile = value !== null;
  const supportingText = hasFile
    ? `${selectedText ?? label}${value ? ` · ${formatFileSize(value.size)}` : ""}`
    : description;

  function selectFile(file: File | null) {
    onChange(file);
  }

  function handleInputChange(event: ChangeEvent<HTMLInputElement>) {
    selectFile(event.currentTarget.files?.[0] ?? null);
    event.currentTarget.value = "";
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    if (disabled) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    setIsDragging(true);
  }

  function handleDragLeave(event: DragEvent<HTMLDivElement>) {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
      setIsDragging(false);
    }
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    if (disabled) return;
    event.preventDefault();
    setIsDragging(false);
    selectFile(event.dataTransfer.files?.[0] ?? null);
  }

  return (
    <section
      className={[
        "vibehub-upload",
        hasFile ? "has-file" : undefined,
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      data-dragging={isDragging ? "true" : undefined}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      <input
        aria-hidden="true"
        accept={accept}
        className="vibehub-upload-input"
        disabled={disabled}
        id={inputId}
        name={inputName}
        onChange={handleInputChange}
        ref={inputRef}
        tabIndex={-1}
        type="file"
      />
      <div className="vibehub-upload-preview">
        <button
          aria-label={hasFile && value ? `${label}: ${value.name}` : label}
          className="vibehub-upload-dropzone"
          disabled={disabled}
          onClick={() => inputRef.current?.click()}
          type="button"
        >
          <span className="vibehub-upload-mark" aria-hidden="true">
            +
          </span>
          <span className="vibehub-upload-prompt">
            {hasFile && value ? value.name : emptyText}
          </span>
          {hasFile && value ? (
            <span className="vibehub-upload-file-meta">{formatFileSize(value.size)}</span>
          ) : null}
        </button>
      </div>
      <div className="vibehub-upload-meta">
        <div className="vibehub-upload-copy">
          <strong>
            {label}
            {actionLabel ? <span>{actionLabel}</span> : null}
          </strong>
          {supportingText ? <p>{supportingText}</p> : null}
        </div>
        {badgeLabel ? <span className="vibehub-upload-badge">{badgeLabel}</span> : null}
      </div>
    </section>
  );
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  const kilobytes = bytes / 1024;
  if (kilobytes < 1024) return `${kilobytes.toFixed(1)} KB`;
  return `${(kilobytes / 1024).toFixed(1)} MB`;
}
