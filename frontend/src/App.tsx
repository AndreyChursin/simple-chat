import { Navigate, Outlet, Route, Routes } from 'react-router-dom';
import { GuestOnly, RequireAuth, useMe } from './auth/session';
import { TopMenu } from './components/TopMenu';
import { ForgotPasswordPage, LoginPage, RegisterPage, ResetPasswordPage } from './pages/AuthPages';
import { ProfilePage } from './pages/ProfilePage';
import { SessionsPage } from './pages/SessionsPage';

function AppLayout() {
  return (
    <RequireAuth>
      <div className="app-shell">
        <TopMenu />
        <Outlet />
      </div>
    </RequireAuth>
  );
}

function HomePage() {
  const me = useMe().data;
  return (
    <main className="page narrow">
      <h1>Welcome, {me?.display_name || me?.username}</h1>
      <p className="muted">Rooms, contacts and messages will appear here.</p>
    </main>
  );
}

/** Menu targets whose screens arrive in later roadmap stages (3: rooms, 6: contacts). */
function NotYetPage({ title }: { title: string }) {
  return (
    <main className="page narrow">
      <h1>{title}</h1>
      <p className="muted">This section is not available yet.</p>
    </main>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<GuestOnly><LoginPage /></GuestOnly>} />
      <Route path="/register" element={<GuestOnly><RegisterPage /></GuestOnly>} />
      <Route path="/forgot-password" element={<GuestOnly><ForgotPasswordPage /></GuestOnly>} />
      {/* Reachable while signed in too: a reset link may be opened in a browser with another session. */}
      <Route path="/reset-password" element={<ResetPasswordPage />} />
      <Route element={<AppLayout />}>
        <Route index element={<HomePage />} />
        <Route path="/sessions" element={<SessionsPage />} />
        <Route path="/profile" element={<ProfilePage />} />
        <Route path="/rooms/catalog" element={<NotYetPage title="Public rooms" />} />
        <Route path="/rooms/private" element={<NotYetPage title="Private rooms" />} />
        <Route path="/contacts" element={<NotYetPage title="Contacts" />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
