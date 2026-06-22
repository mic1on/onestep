import type {
  InputHTMLAttributes,
  ReactNode,
  TextareaHTMLAttributes,
} from "react";

type SharedDialogFieldProps = {
  children?: ReactNode;
  className?: string;
  inputClassName?: string;
  label: ReactNode;
  note?: ReactNode;
  noteClassName?: string;
};

type VibeDialogInputFieldProps = SharedDialogFieldProps &
  Omit<InputHTMLAttributes<HTMLInputElement>, "className" | "children"> & {
    multiline?: false;
  };

type VibeDialogTextareaFieldProps = SharedDialogFieldProps &
  Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "className" | "children"> & {
    multiline: true;
  };

export type VibeDialogFieldProps =
  | VibeDialogInputFieldProps
  | VibeDialogTextareaFieldProps;

export function VibeDialogField(props: VibeDialogFieldProps) {
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
      <label className={["dialog-field", className].filter(Boolean).join(" ")}>
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
    <label className={["dialog-field", className].filter(Boolean).join(" ")}>
      <span>{label}</span>
      <input className={inputClassName} {...inputProps} />
      {note === undefined || note === null ? null : <p className={noteClassName}>{note}</p>}
      {children}
    </label>
  );
}
