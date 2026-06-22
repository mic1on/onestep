import { VibeField } from "../../../components/ui/VibeField";

type WorkerEnvRowProps = {
  envKey: string;
  envValue: string;
  keyAriaLabel: string;
  keyLabel: string;
  keyPlaceholder: string;
  onKeyChange: (value: string) => void;
  onRemove: () => void;
  onValueChange: (value: string) => void;
  removeLabel: string;
  valueAriaLabel: string;
  valueLabel: string;
  valuePlaceholder: string;
};

export function WorkerEnvRow({
  envKey,
  envValue,
  keyAriaLabel,
  keyLabel,
  keyPlaceholder,
  onKeyChange,
  onRemove,
  onValueChange,
  removeLabel,
  valueAriaLabel,
  valueLabel,
  valuePlaceholder,
}: WorkerEnvRowProps) {
  return (
    <div className="worker-env-row">
      <VibeField
        aria-label={keyAriaLabel}
        label={keyLabel}
        onChange={(event) => onKeyChange(event.target.value)}
        placeholder={keyPlaceholder}
        type="text"
        value={envKey}
      />
      <VibeField
        aria-label={valueAriaLabel}
        label={valueLabel}
        onChange={(event) => onValueChange(event.target.value)}
        placeholder={valuePlaceholder}
        type="text"
        value={envValue}
      />
      <button className="worker-env-remove" onClick={onRemove} type="button">
        {removeLabel}
      </button>
    </div>
  );
}
