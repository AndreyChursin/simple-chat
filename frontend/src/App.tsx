import { Navigate, Outlet, Route, Routes } from 'react-router-dom';
import { GuestOnly, RequireAuth } from './auth/session';
import { Toasts } from './components/Toasts';
import { TopMenu } from './components/TopMenu';
import { ChatLayout } from './layout/ChatLayout';
import { ForgotPasswordPage, LoginPage, RegisterPage, ResetPasswordPage } from './pages/AuthPages';
import { CatalogPage } from './pages/CatalogPage';
import { ContactsPage } from './pages/ContactsPage';
import { DmPage } from './pages/DmPage';
import { HomePage } from './pages/HomePage';
import { PrivateRoomsPage } from './pages/PrivateRoomsPage';
import { ProfilePage } from './pages/ProfilePage';
import { RoomPage } from './pages/RoomPage';
import { SessionsPage } from './pages/SessionsPage';
import { SocketBridge } from './ws/SocketBridge';

function AppLayout() {
  return (
    <RequireAuth>
      <div className="app-shell">
        <TopMenu />
        <Outlet />
        <Toasts />
        <SocketBridge />
      </div>
    </RequireAuth>
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
          <Route path="/dm/:dmId" element={<DmPage />} />
        </Route>
        <Route path="/rooms/catalog" element={<CatalogPage />} />
        <Route path="/rooms/private" element={<PrivateRoomsPage />} />
        <Route path="/sessions" element={<SessionsPage />} />
        <Route path="/profile" element={<ProfilePage />} />
        <Route path="/contacts" element={<ContactsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
