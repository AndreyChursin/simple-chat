import type { InputHTMLAttributes, ReactNode } from 'react';
import { ApiError } from '../api/client';

export const PASSWORD_MIN = 8;
export const PASSWORD_MAX = 128;
export const USERNAME_RE = /^[A-Za-z0-9_.-]{3,32}$/;

const FIELD_BY_CODE: Record<string, string> = {
  email_taken: 'email',
  username_taken: 'username',
  invalid_current_password: 'current_password',
};

export type FieldErrors = Record<string, string>;

/** Maps an API error to per-field messages (by error code or validation `loc`) + a form-level one. */
export function apiErrorToFields(error: unknown): { fields: FieldErrors; form: string | null } {
  if (!(error instanceof ApiError)) return { fields: {}, form: 'Network error. Please try again.' };
  const field = FIELD_BY_CODE[error.code];
  if (field) return { fields: { [field]: error.message }, form: null };
  if (error.code === 'validation_error' && Array.isArray(error.details.errors)) {
    const fields: FieldErrors = {};
    for (const e of error.details.errors as { loc?: unknown[]; msg?: string }[]) {
      const name = e.loc?.[1];
      if (typeof name === 'string') fields[name] = e.msg ?? 'Invalid value';
    }
    if (Object.keys(fields).length) return { fields, form: null };
  }
  return { fields: {}, form: error.message };
}

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  name: string;
  error?: string;
  hint?: ReactNode;
}

export function Field({ label, name, error, hint, ...input }: FieldProps) {
  const id = `f-${name}`;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        name={name}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-err` : hint ? `${id}-hint` : undefined}
        {...input}
      />
      {hint && !error && (
        <small id={`${id}-hint`} className="hint">
          {hint}
        </small>
      )}
      {error && (
        <small id={`${id}-err`} className="field-error" role="alert">
          {error}
        </small>
      )}
    </div>
  );
}

export function FormError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div className="form-error" role="alert">
      {message}
    </div>
  );
}

export function FormNotice({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div className="form-notice" role="status">
      {message}
    </div>
  );
}
