import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { authApi, tokenStore, UNAUTHORIZED_EVENT } from '../services/api';

const AuthContext = createContext(null);

/** status: 'loading' | 'authenticated' | 'anonymous' */
export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [status, setStatus] = useState('loading');

  const clearSession = useCallback(() => {
    tokenStore.clear();
    setUser(null);
    setStatus('anonymous');
  }, []);

  // Restore the session on page load (e.g. after refresh)
  useEffect(() => {
    let cancelled = false;

    async function restoreSession() {
      if (!tokenStore.get()) {
        setStatus('anonymous');
        return;
      }
      try {
        const me = await authApi.me();
        if (!cancelled) {
          setUser(me);
          setStatus('authenticated');
        }
      } catch {
        if (!cancelled) clearSession();
      }
    }

    restoreSession();
    return () => {
      cancelled = true;
    };
  }, [clearSession]);

  // Any API call that gets a 401 logs the user out
  useEffect(() => {
    window.addEventListener(UNAUTHORIZED_EVENT, clearSession);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, clearSession);
  }, [clearSession]);

  const login = useCallback(async (username, password) => {
    const { access_token: token } = await authApi.login(username, password);
    tokenStore.set(token);
    const me = await authApi.me();
    setUser(me);
    setStatus('authenticated');
    return me;
  }, []);

  const logout = useCallback(() => clearSession(), [clearSession]);

  const value = useMemo(() => ({ user, status, login, logout }), [user, status, login, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used inside AuthProvider');
  return context;
}
