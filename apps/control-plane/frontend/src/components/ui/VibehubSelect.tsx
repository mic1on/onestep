import {
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";

export type VibehubSelectOption = {
  value: string;
  label: string;
  disabled?: boolean;
};

type VibehubSelectProps = {
  label: string;
  options: VibehubSelectOption[];
  value: string;
  onChange?: (value: string) => void;
  className?: string;
  disabled?: boolean;
  placeholder?: string;
};

export function VibehubSelect({
  label,
  options,
  value,
  onChange,
  className,
  disabled = false,
  placeholder,
}: VibehubSelectProps) {
  const generatedId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const selectedIndex = options.findIndex((option) => option.value === value);
  const selectedOption = selectedIndex >= 0 ? options[selectedIndex] : null;
  const [activeIndex, setActiveIndex] = useState(() =>
    selectedIndex >= 0 ? selectedIndex : firstEnabledIndex(options),
  );
  const triggerId = `vibehub-select-${generatedId}`;
  const listboxId = `${triggerId}-listbox`;

  useEffect(() => {
    if (!isOpen) return;
    setActiveIndex(selectedIndex >= 0 ? selectedIndex : firstEnabledIndex(options));
  }, [isOpen, options, selectedIndex]);

  useEffect(() => {
    if (!isOpen) return;

    function handlePointerDown(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }

    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [isOpen]);

  function selectOption(index: number) {
    const option = options[index];
    if (!option || option.disabled) return;
    onChange?.(option.value);
    setIsOpen(false);
  }

  function openWithIndex(index: number) {
    const fallbackIndex = selectedIndex >= 0 ? selectedIndex : firstEnabledIndex(options);
    setActiveIndex(index >= 0 ? index : fallbackIndex);
    setIsOpen(true);
  }

  function moveActive(direction: 1 | -1) {
    setActiveIndex((current) => nextEnabledIndex(options, current, direction));
  }

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (disabled) return;

    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (!isOpen) {
        openWithIndex(selectedIndex >= 0 ? selectedIndex : firstEnabledIndex(options));
        return;
      }
      moveActive(1);
      return;
    }

    if (event.key === "ArrowUp") {
      event.preventDefault();
      if (!isOpen) {
        openWithIndex(selectedIndex >= 0 ? selectedIndex : lastEnabledIndex(options));
        return;
      }
      moveActive(-1);
      return;
    }

    if (event.key === "Home") {
      event.preventDefault();
      openWithIndex(firstEnabledIndex(options));
      return;
    }

    if (event.key === "End") {
      event.preventDefault();
      openWithIndex(lastEnabledIndex(options));
      return;
    }

    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (!isOpen) {
        openWithIndex(selectedIndex >= 0 ? selectedIndex : firstEnabledIndex(options));
        return;
      }
      selectOption(activeIndex);
      return;
    }

    if (event.key === "Escape") {
      setIsOpen(false);
    }
  }

  return (
    <div
      className={[
        "ref-inline-control",
        "ref-inline-control-select",
        "ref-select-field",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      data-open={isOpen ? "true" : undefined}
      ref={rootRef}
    >
      <span className="ref-select-label">{label}</span>
      <button
        aria-activedescendant={
          isOpen && activeIndex >= 0 ? `${triggerId}-option-${activeIndex}` : undefined
        }
        aria-controls={listboxId}
        aria-expanded={isOpen}
        aria-haspopup="listbox"
        aria-label={label}
        className="ref-select-trigger"
        disabled={disabled}
        id={triggerId}
        onClick={() => setIsOpen((current) => !current)}
        onKeyDown={handleKeyDown}
        role="combobox"
        type="button"
      >
        <span className={selectedOption ? "ref-select-value" : "ref-select-value is-placeholder"}>
          {selectedOption?.label ?? placeholder ?? ""}
        </span>
        <span className="ref-select-caret" aria-hidden="true" />
      </button>

      {isOpen ? (
        <div aria-label={label} className="ref-select-popover" id={listboxId} role="listbox">
          {options.map((option, index) => (
            <button
              aria-selected={option.value === value}
              className={[
                "ref-select-option",
                option.value === value ? "is-selected" : undefined,
                index === activeIndex ? "is-active" : undefined,
              ]
                .filter(Boolean)
                .join(" ")}
              disabled={option.disabled}
              id={`${triggerId}-option-${index}`}
              key={option.value}
              onClick={() => selectOption(index)}
              onMouseEnter={() => {
                if (!option.disabled) setActiveIndex(index);
              }}
              role="option"
              type="button"
            >
              <span>{option.label}</span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function firstEnabledIndex(options: VibehubSelectOption[]) {
  return options.findIndex((option) => !option.disabled);
}

function lastEnabledIndex(options: VibehubSelectOption[]) {
  for (let index = options.length - 1; index >= 0; index -= 1) {
    if (!options[index].disabled) return index;
  }
  return -1;
}

function nextEnabledIndex(
  options: VibehubSelectOption[],
  currentIndex: number,
  direction: 1 | -1,
) {
  if (options.length === 0) return -1;

  let index = currentIndex;
  for (let step = 0; step < options.length; step += 1) {
    index = (index + direction + options.length) % options.length;
    if (!options[index].disabled) return index;
  }
  return -1;
}
