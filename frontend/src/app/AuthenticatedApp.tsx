import { DashboardApp } from './DashboardApp';
import { AuthStatusView, LoginView } from '../features/auth/LocalAuthView';
import { useLocalAuth } from '../hooks/useLocalAuth';

export function AuthenticatedApp() {
  const auth = useLocalAuth();

  if (auth.state.status === 'authenticated') {
    return <DashboardApp onLogout={() => void auth.logout()} user={auth.state.user} />;
  }
  if (auth.state.status === 'anonymous') {
    return <LoginView error={auth.state.error} pending={auth.loginPending} onLogin={auth.login} />;
  }
  if (auth.state.status === 'unavailable') {
    return (
      <AuthStatusView
        actionLabel="Reintentar"
        message={auth.state.message}
        onAction={() => void auth.retryBootstrap()}
      />
    );
  }
  if (auth.state.status === 'logout_failed') {
    return (
      <AuthStatusView
        actionLabel="Reintentar cierre"
        message={auth.state.message}
        onAction={() => void auth.retryLogout()}
      />
    );
  }
  return (
    <AuthStatusView
      busy
      message={auth.state.status === 'logging_out' ? 'Cerrando la sesion de forma segura...' : 'Comprobando el acceso local...'}
    />
  );
}
