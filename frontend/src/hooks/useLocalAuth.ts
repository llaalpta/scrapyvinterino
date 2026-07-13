import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ApiError,
  AUTHENTICATION_REQUIRED_EVENT,
  fetchLocalAuthSession,
  loginLocalUser,
  logoutLocalUser,
  setLocalCsrfToken,
  type LocalAuthSession,
  type LocalAuthUser
} from '../api';

export type LocalAuthState =
  | { status: 'checking' }
  | { status: 'anonymous'; error: string | null }
  | { status: 'authenticated'; user: LocalAuthUser; expiresAt: string }
  | { status: 'unavailable'; message: string }
  | { status: 'logging_out' }
  | { status: 'logout_failed'; message: string };

let bootstrapPromise: Promise<LocalAuthSession> | null = null;

function loadBootstrapSession(): Promise<LocalAuthSession> {
  if (bootstrapPromise === null) {
    bootstrapPromise = fetchLocalAuthSession().finally(() => {
      bootstrapPromise = null;
    });
  }
  return bootstrapPromise;
}

export function useLocalAuth() {
  const [state, setState] = useState<LocalAuthState>({ status: 'checking' });
  const [loginPending, setLoginPending] = useState(false);
  const generationRef = useRef(0);
  const loginInFlightRef = useRef(false);
  const statusRef = useRef<LocalAuthState['status']>('checking');

  const applySession = useCallback((session: LocalAuthSession) => {
    if (session.authenticated && session.user) {
      statusRef.current = 'authenticated';
      setState({ status: 'authenticated', user: session.user, expiresAt: session.expires_at });
      return;
    }
    statusRef.current = 'anonymous';
    setState({ status: 'anonymous', error: null });
  }, []);

  const bootstrap = useCallback(async () => {
    const generation = ++generationRef.current;
    statusRef.current = 'checking';
    setLoginPending(false);
    setState({ status: 'checking' });
    try {
      const session = await loadBootstrapSession();
      if (generationRef.current === generation) {
        applySession(session);
      }
    } catch (caught) {
      setLocalCsrfToken(null);
      if (generationRef.current === generation) {
        statusRef.current = 'unavailable';
        setState({ status: 'unavailable', message: authUnavailableMessage(caught) });
      }
    }
  }, [applySession]);

  useEffect(() => {
    statusRef.current = state.status;
  }, [state.status]);

  useEffect(() => {
    const generation = ++generationRef.current;
    void loadBootstrapSession()
      .then((session) => {
        if (generationRef.current === generation) {
          applySession(session);
        }
      })
      .catch((caught: unknown) => {
        setLocalCsrfToken(null);
        if (generationRef.current === generation) {
          statusRef.current = 'unavailable';
          setState({ status: 'unavailable', message: authUnavailableMessage(caught) });
        }
      });
  }, [applySession]);

  useEffect(() => {
    const handleAuthenticationRequired = () => {
      if (statusRef.current !== 'authenticated') {
        return;
      }
      statusRef.current = 'checking';
      setLocalCsrfToken(null);
      void bootstrap();
    };
    window.addEventListener(AUTHENTICATION_REQUIRED_EVENT, handleAuthenticationRequired);
    return () => window.removeEventListener(AUTHENTICATION_REQUIRED_EVENT, handleAuthenticationRequired);
  }, [bootstrap]);

  const login = useCallback(async (email: string, password: string) => {
    if (loginInFlightRef.current) {
      return;
    }
    loginInFlightRef.current = true;
    const generation = ++generationRef.current;
    setLoginPending(true);
    setState({ status: 'anonymous', error: null });
    try {
      const session = await loginLocalUser(email, password);
      if (!session.authenticated || !session.user) {
        throw new ApiError(503, 'La API no confirmo una sesion autenticada');
      }
      if (generationRef.current === generation) {
        applySession(session);
      }
    } catch (caught) {
      if (caught instanceof ApiError && (caught.status === 401 || caught.status === 403)) {
        try {
          await fetchLocalAuthSession();
        } catch {
          setLocalCsrfToken(null);
        }
        if (generationRef.current === generation) {
          setState({ status: 'anonymous', error: 'Email o password incorrectos' });
        }
      } else if (generationRef.current === generation) {
        statusRef.current = 'unavailable';
        setState({ status: 'unavailable', message: authUnavailableMessage(caught) });
      }
    } finally {
      loginInFlightRef.current = false;
      if (generationRef.current === generation) {
        setLoginPending(false);
      }
    }
  }, [applySession]);

  const performLogout = useCallback(async (refreshSessionFirst: boolean) => {
    const generation = ++generationRef.current;
    let logoutConfirmed = false;
    statusRef.current = 'logging_out';
    setState({ status: 'logging_out' });
    try {
      if (refreshSessionFirst) {
        const currentSession = await fetchLocalAuthSession();
        if (!currentSession.authenticated || !currentSession.user) {
          if (generationRef.current === generation) {
            applySession(currentSession);
          }
          return;
        }
      }
      await logoutLocalUser();
      logoutConfirmed = true;
      setLocalCsrfToken(null);
      const session = await fetchLocalAuthSession();
      if (generationRef.current === generation) {
        applySession(session);
      }
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        setLocalCsrfToken(null);
        try {
          const session = await fetchLocalAuthSession();
          if (generationRef.current === generation) {
            applySession(session);
          }
          return;
        } catch (bootstrapError) {
          if (generationRef.current === generation) {
            statusRef.current = 'unavailable';
            setState({ status: 'unavailable', message: authUnavailableMessage(bootstrapError) });
          }
          return;
        }
      }
      if (generationRef.current === generation) {
        if (logoutConfirmed) {
          statusRef.current = 'unavailable';
          setState({
            status: 'unavailable',
            message: 'La sesion se cerro, pero no se pudo preparar un nuevo acceso local'
          });
          return;
        }
        statusRef.current = 'logout_failed';
        setState({
          status: 'logout_failed',
          message: 'No se pudo confirmar el cierre de sesion. El panel permanece bloqueado.'
        });
      }
    }
  }, [applySession]);

  const logout = useCallback(() => performLogout(false), [performLogout]);
  const retryLogout = useCallback(() => performLogout(true), [performLogout]);

  return {
    state,
    loginPending,
    login,
    logout,
    retryBootstrap: bootstrap,
    retryLogout
  };
}

function authUnavailableMessage(caught: unknown): string {
  if (caught instanceof ApiError && caught.status === 503) {
    return 'El servicio de autenticacion no esta disponible';
  }
  if (caught instanceof Error && caught.message) {
    return caught.message;
  }
  return 'No se pudo comprobar la sesion local';
}
