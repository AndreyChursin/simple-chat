import { useState, type FormEvent, type ReactNode } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { forgotPassword, login, register, resetPassword } from '../api/auth';
import { ApiError } from '../api/client';
import { takeAccountDeleted, useSetMe } from '../auth/session';
import {
  apiErrorToFields,
  Field,
  FormError,
  FormNotice,
  PASSWORD_MAX,
  PASSWORD_MIN,
  USERNAME_RE,
  type FieldErrors,
} from '../components/forms';

function AuthCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main className="auth-page">
      <div className="auth-card">
        <div className="auth-logo" aria-hidden>
          💬
        </div>
        <h1>{title}</h1>
        {children}
      </div>
    </main>
  );
}

function validatePassword(password: string, confirm: string, field = 'password'): FieldErrors {
  if (password.length < PASSWORD_MIN) return { [field]: `At least ${PASSWORD_MIN} characters` };
  if (password.length > PASSWORD_MAX) return { [field]: `At most ${PASSWORD_MAX} characters` };
  if (password !== confirm) return { confirm: 'Passwords do not match' };
  return {};
}

export function LoginPage() {
  const setMe = useSetMe();
  const [deleted] = useState(takeAccountDeleted);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [keep, setKeep] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      // <GuestOnly> redirects to `next` once the user is cached.
      setMe(await login(email.trim(), password, keep));
    } catch (err) {
      if (err instanceof ApiError && err.code === 'invalid_credentials') setError('Invalid e-mail or password');
      else setError(apiErrorToFields(err).form ?? 'Sign in failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard title="Sign in">
      <form onSubmit={onSubmit} noValidate>
        {deleted && <FormNotice message="Your account was deleted." />}
        <FormError message={error} />
        <Field label="E-mail" name="email" type="email" autoComplete="email" required autoFocus
          value={email} onChange={(e) => setEmail(e.target.value)} />
        <Field label="Password" name="password" type="password" autoComplete="current-password" required
          value={password} onChange={(e) => setPassword(e.target.value)} />
        <label className="checkbox">
          <input type="checkbox" checked={keep} onChange={(e) => setKeep(e.target.checked)} />
          Keep me signed in
        </label>
        <button className="btn primary wide" type="submit" disabled={busy || !email || !password}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
      <div className="auth-links">
        <Link to="/forgot-password">Forgot password?</Link>
        <Link to="/register">Create account</Link>
      </div>
    </AuthCard>
  );
}

export function RegisterPage() {
  const setMe = useSetMe();
  const [form, setForm] = useState({ email: '', username: '', password: '', confirm: '' });
  const [fields, setFields] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [k]: e.target.value });

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    const errs: FieldErrors = {};
    if (!form.email.includes('@')) errs.email = 'Enter a valid e-mail';
    if (!USERNAME_RE.test(form.username)) errs.username = '3–32 characters: letters, digits, _ . -';
    Object.assign(errs, validatePassword(form.password, form.confirm));
    setFields(errs);
    if (Object.keys(errs).length) return;

    setBusy(true);
    try {
      setMe(await register(form.email.trim(), form.username, form.password));
    } catch (err) {
      const mapped = apiErrorToFields(err);
      setFields(mapped.fields);
      setFormError(mapped.form);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard title="Create account">
      <form onSubmit={onSubmit} noValidate>
        <FormError message={formError} />
        <Field label="E-mail" name="email" type="email" autoComplete="email" autoFocus
          value={form.email} onChange={set('email')} error={fields.email} />
        <Field label="Username" name="username" autoComplete="username" value={form.username}
          onChange={set('username')} error={fields.username}
          hint={<>3–32 characters: letters, digits, <code>_ . -</code>. <strong>Username can't be changed later.</strong></>} />
        <Field label="Password" name="password" type="password" autoComplete="new-password"
          value={form.password} onChange={set('password')} error={fields.password}
          hint={`At least ${PASSWORD_MIN} characters`} />
        <Field label="Confirm password" name="confirm" type="password" autoComplete="new-password"
          value={form.confirm} onChange={set('confirm')} error={fields.confirm} />
        <button className="btn primary wide" type="submit" disabled={busy}>
          {busy ? 'Creating…' : 'Create account'}
        </button>
      </form>
      <div className="auth-links">
        <span>
          Already registered? <Link to="/login">Sign in</Link>
        </span>
      </div>
    </AuthCard>
  );
}

export function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await forgotPassword(email.trim());
      setSent(true);
    } catch (err) {
      setError(apiErrorToFields(err).form);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard title="Reset password">
      {sent ? (
        <FormNotice message="If an account with this e-mail exists, we have sent a link to reset the password. The link is valid for 1 hour." />
      ) : (
        <form onSubmit={onSubmit} noValidate>
          <FormError message={error} />
          <Field label="E-mail" name="email" type="email" autoComplete="email" autoFocus
            value={email} onChange={(e) => setEmail(e.target.value)} />
          <button className="btn primary wide" type="submit" disabled={busy || !email.includes('@')}>
            {busy ? 'Sending…' : 'Send reset link'}
          </button>
        </form>
      )}
      <div className="auth-links">
        <Link to="/login">Back to sign in</Link>
      </div>
    </AuthCard>
  );
}

export function ResetPasswordPage() {
  const [params] = useSearchParams();
  const token = params.get('token');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [fields, setFields] = useState<FieldErrors>({});
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!token) return;
    const errs = validatePassword(password, confirm);
    setFields(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    setError(null);
    try {
      await resetPassword(token, password);
      setDone(true);
    } catch (err) {
      if (err instanceof ApiError && err.code === 'reset_token_invalid') {
        setError('This link has expired or was already used. Request a new one.');
      } else {
        const mapped = apiErrorToFields(err);
        setFields(mapped.fields.new_password ? { password: mapped.fields.new_password } : {});
        setError(mapped.form);
      }
    } finally {
      setBusy(false);
    }
  }

  if (!token) {
    return (
      <AuthCard title="Reset password">
        <FormError message="The reset link is incomplete." />
        <div className="auth-links">
          <Link to="/forgot-password">Request a new link</Link>
        </div>
      </AuthCard>
    );
  }

  return (
    <AuthCard title="Choose a new password">
      {done ? (
        <>
          <FormNotice message="Your password was changed. All sessions were signed out." />
          <Link className="btn primary wide" to="/login">Sign in</Link>
        </>
      ) : (
        <form onSubmit={onSubmit} noValidate>
          <FormError message={error} />
          <Field label="New password" name="password" type="password" autoComplete="new-password" autoFocus
            value={password} onChange={(e) => setPassword(e.target.value)} error={fields.password} />
          <Field label="Confirm password" name="confirm" type="password" autoComplete="new-password"
            value={confirm} onChange={(e) => setConfirm(e.target.value)} error={fields.confirm} />
          <button className="btn primary wide" type="submit" disabled={busy}>
            {busy ? 'Saving…' : 'Set new password'}
          </button>
          {error && (
            <div className="auth-links">
              <Link to="/forgot-password">Request a new link</Link>
            </div>
          )}
        </form>
      )}
    </AuthCard>
  );
}
