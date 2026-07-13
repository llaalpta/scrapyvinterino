import { LockKeyhole, LogIn, RefreshCw } from 'lucide-react';
import { useState, type FormEvent } from 'react';

export function LoginView({
  error,
  pending,
  onLogin
}: {
  error: string | null;
  pending: boolean;
  onLogin: (email: string, password: string) => Promise<void>;
}) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const submittedPassword = password;
    setPassword('');
    await onLogin(email.trim(), submittedPassword);
  }

  return (
    <main className="auth-shell">
      <section className="auth-card" aria-labelledby="login-title">
        <div className="auth-brand" aria-hidden="true">
          <LockKeyhole size={28} />
        </div>
        <p className="eyebrow">Panel privado</p>
        <h1 id="login-title">Acceso a Vinted Monitor</h1>
        <p className="auth-copy">Inicia sesion con el usuario local autorizado para consultar o modificar el monitor.</p>
        <form className="auth-form" onSubmit={(event) => void submit(event)}>
          <label>
            Email
            <input
              autoComplete="username"
              autoFocus
              disabled={pending}
              onChange={(event) => setEmail(event.target.value)}
              required
              type="email"
              value={email}
            />
          </label>
          <label>
            Password
            <input
              autoComplete="current-password"
              disabled={pending}
              minLength={12}
              onChange={(event) => setPassword(event.target.value)}
              required
              type="password"
              value={password}
            />
          </label>
          {error ? <p className="auth-error" role="alert">{error}</p> : null}
          <button disabled={pending || email.trim() === '' || password === ''} type="submit">
            <LogIn size={18} />
            {pending ? 'Comprobando...' : 'Entrar'}
          </button>
        </form>
      </section>
    </main>
  );
}

export function AuthStatusView({
  actionLabel,
  busy = false,
  message,
  onAction
}: {
  actionLabel?: string;
  busy?: boolean;
  message: string;
  onAction?: () => void;
}) {
  return (
    <main className="auth-shell">
      <section className="auth-card auth-status" aria-live="polite">
        <div className="auth-brand" aria-hidden="true">
          {busy ? <RefreshCw className="auth-spinner" size={28} /> : <LockKeyhole size={28} />}
        </div>
        <h1>Vinted Monitor</h1>
        <p className="auth-copy">{message}</p>
        {onAction && actionLabel ? (
          <button type="button" onClick={onAction}>
            <RefreshCw size={18} />
            {actionLabel}
          </button>
        ) : null}
      </section>
    </main>
  );
}
