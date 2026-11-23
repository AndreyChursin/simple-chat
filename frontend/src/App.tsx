import { Navigate, Outlet, Route, Routes } from 'react-router-dom';
import { GuestOnly, RequireAuth } from './auth/session';
import { Toasts } from './components/Toasts';
import { TopMenu } from './components/TopMenu';
import { ChatLayout } from './layout/ChatLayout';
import { ForgotPasswordPage, LoginPage, RegisterPage, ResetPasswordPage } from './pages/AuthPages';
import { CatalogPage } from './pages/CatalogPage';
import { HomePage } from './pages/HomePage';
import { PrivateRoomsPage } from './pages/PrivateRoomsPage';
import { ProfilePage } from './pages/ProfilePage';
import { RoomPage } from './pages/RoomPage';
import { SessionsPage } from './pages/SessionsPage';

function AppLayout() {
  return (
    <RequireAuth>
      <div className="app-shell">
        <TopMenu />
        <Outlet />
        <Toasts />
      </div>
    </RequireAuth>
  );
}

/** Menu targets whose screens arrive in later roadmap stages (6: contacts). */
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
        <Route element={<ChatLayout />}>
          <Route index element={<HomePage />} />
          <Route path="/rooms/:roomId" element={<RoomPage />} />
        </Route>
        <Route path="/rooms/catalog" element={<CatalogPage />} />
        <Route path="/rooms/private" element={<PrivateRoomsPage />} />
        <Route path="/sessions" element={<SessionsPage />} />
        <Route path="/profile" element={<ProfilePage />} />
        <Route path="/contacts" element={<NotYetPage title="Contacts" />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
