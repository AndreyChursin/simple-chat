import { useEffect, useState, type FormEvent } from 'react';
import { useLocation } from 'react-router-dom';
import { changePassword, updateProfile } from '../api/auth';
import { useMe, useSetMe } from '../auth/session';
import {
  apiErrorToFields,
  Field,
  FormError,
  FormNotice,
  PASSWORD_MAX,
  PASSWORD_MIN,
  type FieldErrors,
} from '../components/forms';

function ProfileForm() {
  const me = useMe().data;
  const setMe = useSetMe();
  const [displayName, setDisplayName] = useState(me?.display_name ?? '');
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!me) return null;

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setNotice(null);
    setError(null);
    try {
      const updated = await updateProfile(displayName.trim() || null);
      setMe(updated);
      setDisplayName(updated.display_name ?? '');
      setNotice('Profile saved');
    } catch (err) {
      setError(apiErrorToFields(err).form ?? 'Could not save profile');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Profile</h2>
      <form onSubmit={onSubmit} noValidate>
        <FormError message={error} />
        <FormNotice message={notice} />
        <Field label="E-mail" name="email" value={me.email} readOnly disabled />
        <Field label="Username" name="username" value={me.username} readOnly disabled
          hint="Username cannot be changed" />
        <Field label="Display name" name="display_name" maxLength={64} placeholder="Optional"
          value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        <button className="btn primary" type="submit" disabled={busy}>
          Save
        </button>
      </form>
    </section>
  );
}

function ChangePasswordForm() {
  const empty = { current_password: '', new_password: '', confirm: '' };
  const [form, setForm] = useState(empty);
  const [fields, setFields] = useState<FieldErrors>({});
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [k]: e.target.value });

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setNotice(null);
    setError(null);
    const errs: FieldErrors = {};
    if (!form.current_password) errs.current_password = 'Required';
    if (form.new_password.length < PASSWORD_MIN) errs.new_password = `At least ${PASSWORD_MIN} characters`;
    else if (form.new_password.length > PASSWORD_MAX) errs.new_password = `At most ${PASSWORD_MAX} characters`;
    else if (form.new_password !== form.confirm) errs.confirm = 'Passwords do not match';
    setFields(errs);
    if (Object.keys(errs).length) return;

    setBusy(true);
    try {
      await changePassword(form.current_password, form.new_password);
      setForm(empty);
      setNotice('Password changed. Your other sessions were signed out.');
    } catch (err) {
      const mapped = apiErrorToFields(err);
      setFields(mapped.fields);
      setError(mapped.form);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" id="password">
      <h2>Change password</h2>
      <form onSubmit={onSubmit} noValidate>
        <FormError message={error} />
        <FormNotice message={notice} />
        <Field label="Current password" name="current_password" type="password" autoComplete="current-password"
          value={form.current_password} onChange={set('current_password')} error={fields.current_password} />
        <Field label="New password" name="new_password" type="password" autoComplete="new-password"
          value={form.new_password} onChange={set('new_password')} error={fields.new_password} />
        <Field label="Confirm new password" name="confirm" type="password" autoComplete="new-password"
          value={form.confirm} onChange={set('confirm')} error={fields.confirm} />
        <button className="btn primary" type="submit" disabled={busy}>
          Change password
        </button>
      </form>
    </section>
  );
}

export function ProfilePage() {
  const { hash } = useLocation();
  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView({ behavior: 'smooth' });
  }, [hash]);

  return (
    <main className="page narrow">
      <h1>Profile settings</h1>
      <ProfileForm />
      <ChangePasswordForm />
    </main>
  );
}
