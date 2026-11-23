import { fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { safeNext } from './auth/session';
import { apiError, json, ME, mockApi, renderApp } from './test/utils';

afterEach(() => {
  vi.unstubAllGlobals();
});

const SESSIONS = [
  {
    id: 7, browser: 'Chrome 131', os: 'Windows 10', device: 'Desktop', user_agent: 'UA1', ip: '10.0.0.1',
    created_at: '2026-11-23T10:00:00Z', last_seen_at: '2026-11-23T10:00:00Z', persistent: false, is_current: true,
  },
  {
    id: 8, browser: 'Mobile Safari 17', os: 'iOS 17', device: 'Mobile', user_agent: 'UA2', ip: '10.0.0.2',
    created_at: '2026-11-22T10:00:00Z', last_seen_at: '2026-11-22T12:00:00Z', persistent: true, is_current: false,
  },
];

describe('auth routing', () => {
  it('redirects anonymous users to the sign-in page', async () => {
    mockApi({ 'GET /api/auth/me': () => apiError(401, 'not_authenticated') });
    renderApp('/sessions');
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
  });

  it('shows a generic message on wrong credentials and sends keep_signed_in', async () => {
    const calls = mockApi({
      'GET /api/auth/me': () => apiError(401, 'not_authenticated'),
      'POST /api/auth/login': () => apiError(401, 'invalid_credentials', 'Invalid e-mail or password'),
    });
    renderApp('/login');
    fireEvent.change(await screen.findByLabelText('E-mail'), { target: { value: 'a@example.com' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'wrong-pass' } });
    fireEvent.click(screen.getByLabelText('Keep me signed in'));
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid e-mail or password');
    const body = JSON.parse(String(calls.find((c) => c.key === 'POST /api/auth/login')?.init?.body));
    expect(body).toEqual({ email: 'a@example.com', password: 'wrong-pass', keep_signed_in: true });
  });

  it('signs in and lands on the requested page', async () => {
    mockApi({
      'GET /api/auth/me': () => apiError(401, 'not_authenticated'),
      'POST /api/auth/login': () => json({ user: ME }),
      'GET /api/sessions': () => json({ items: SESSIONS }),
    });
    renderApp('/login?next=%2Fsessions');
    fireEvent.change(await screen.findByLabelText('E-mail'), { target: { value: 'alice@example.com' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'correct-horse' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('heading', { name: 'Active sessions' })).toBeInTheDocument();
  });

  it('only accepts relative post-login redirects', () => {
    expect(safeNext('/rooms/1')).toBe('/rooms/1');
    expect(safeNext('//evil.example')).toBe('/');
    expect(safeNext('https://evil.example')).toBe('/');
    expect(safeNext(null)).toBe('/');
  });
});

describe('register', () => {
  it('maps username_taken to the username field', async () => {
    mockApi({
      'GET /api/auth/me': () => apiError(401, 'not_authenticated'),
      'POST /api/auth/register': () => apiError(409, 'username_taken', 'This username is already taken'),
    });
    renderApp('/register');
    fireEvent.change(await screen.findByLabelText('E-mail'), { target: { value: 'b@example.com' } });
    fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'alice' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'long-enough' } });
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'long-enough' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
    expect(await screen.findByText('This username is already taken')).toBeInTheDocument();
    expect(screen.getByLabelText('Username')).toHaveAttribute('aria-invalid', 'true');
  });

  it('validates locally before calling the API', async () => {
    const calls = mockApi({ 'GET /api/auth/me': () => apiError(401, 'not_authenticated') });
    renderApp('/register');
    fireEvent.change(await screen.findByLabelText('Username'), { target: { value: 'a b' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'short' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));
    expect(await screen.findByText('At least 8 characters')).toBeInTheDocument();
    expect(calls.some((c) => c.key.includes('/register'))).toBe(false);
  });
});

describe('sessions page', () => {
  it('marks the current device and revokes another session', async () => {
    let sessions = SESSIONS;
    const calls = mockApi({
      'GET /api/auth/me': () => json({ user: ME }),
      'GET /api/sessions': () => json({ items: sessions }),
      'DELETE /api/sessions/8': () => {
        sessions = SESSIONS.slice(0, 1);
        return json(undefined, 204);
      },
    });
    renderApp('/sessions');
    expect(await screen.findByText('This device')).toBeInTheDocument();
    expect(screen.getByText('10.0.0.2')).toBeInTheDocument();
    const buttons = screen.getAllByRole('button', { name: 'Sign out' });
    // [0] is the top menu, [1] current session row, [2] the phone
    fireEvent.click(buttons[2]!);
    await waitFor(() => expect(screen.queryByText('10.0.0.2')).not.toBeInTheDocument());
    expect(calls.some((c) => c.key === 'DELETE /api/sessions/8')).toBe(true);
  });

  it('a 401 from any request returns to sign-in (session revoked elsewhere)', async () => {
    mockApi({
      'GET /api/auth/me': () => json({ user: ME }),
      'GET /api/sessions': () => apiError(401, 'not_authenticated'),
    });
    renderApp('/sessions');
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
  });
});

describe('profile', () => {
  it('shows username as read-only', async () => {
    mockApi({ 'GET /api/auth/me': () => json({ user: ME }) });
    renderApp('/profile');
    const username = await screen.findByLabelText('Username');
    expect(username).toHaveValue('alice');
    expect(username).toBeDisabled();
    expect(screen.getByText('Username cannot be changed')).toBeInTheDocument();
  });

  it('reports a wrong current password on the field', async () => {
    mockApi({
      'GET /api/auth/me': () => json({ user: ME }),
      'POST /api/auth/password/change': () =>
        apiError(401, 'invalid_current_password', 'Current password is incorrect'),
    });
    renderApp('/profile');
    fireEvent.change(await screen.findByLabelText('Current password'), { target: { value: 'nope' } });
    fireEvent.change(screen.getByLabelText('New password'), { target: { value: 'brand-new-1' } });
    fireEvent.change(screen.getByLabelText('Confirm new password'), { target: { value: 'brand-new-1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Change password' }));
    expect(await screen.findByText('Current password is incorrect')).toBeInTheDocument();
  });
});
