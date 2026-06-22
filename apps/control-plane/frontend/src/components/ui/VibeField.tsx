import type {
  InputHTMLAttributes,
  ReactNode,
  TextareaHTMLAttributes,
} from "react";

type SharedFieldProps = {
  children?: ReactNode;
  className?: string;
  inputClassName?: string;
  label: ReactNode;
  note?: ReactNode;
  noteClassName?: string;
};

type VibeInputFieldProps = SharedFieldProps &
  Omit<InputHTMLAttributes<HTMLInputElement>, "className" | "children"> & {
    multiline?: false;
  };

type VibeTextareaFieldProps = SharedFieldProps &
  Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "className" | "children"> & {
    multiline: true;
  };

export type VibeFieldProps = VibeInputFieldProps | VibeTextareaFieldProps;

export function VibeField(props: VibeFieldProps) {
  if (props.multiline) {
    const {
      children,
      className,
      inputClassName,
      label,
      multiline,
      note,
      noteClassName,
      ...textareaProps
    } = props;

    return (
      <label className={["ref-inline-control", className].filter(Boolean).join(" ")}>
        <span>{label}</span>
        <textarea className={inputClassName} {...textareaProps} />
        {note === undefined || note === null ? null : <p className={noteClassName}>{note}</p>}
        {children}
      </label>
    );
  }

  const {
    children,
    className,
    inputClassName,
    label,
    multiline,
    note,
    noteClassName,
    ...inputProps
  } = props;

  return (
    <label className={["ref-inline-control", className].filter(Boolean).join(" ")}>
      <span>{label}</span>
      <input className={inputClassName} {...inputProps} />
      {note === undefined || note === null ? null : <p className={noteClassName}>{note}</p>}
      {children}
    </label>
  );
}
