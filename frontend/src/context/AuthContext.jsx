/**
 * AuthContext.jsx — Global authentication state.
 *
 * NOT wired into the UI yet. Activate by:
 *   1. Wrapping <App /> with <AuthProvider> in main.jsx
 *   2. Using <ProtectedRoute> in App.jsx for protected pages
 *
 * Design:
 *  - On mount: attempts getMe() if an access token exists in localStorage.
 *    If it fails (expired), tries refreshTokens() automatically.
 *  - Exposes: user, loading, login(), logout(), isAdmin(), isAnalyst()
 */
import { createContext, useContext, useState, useEffect, useCallback } from "react";
import {
  login as apiLogin,
  logout as apiLogout,
  getMe,
  refreshTokens,
  getStoredUser,
  isAuthenticated as hasToken,
} from "../api/authApi";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user,    setUser]    = useState(null);
  const [loading, setLoading] = useState(true);
  const [error,   setError]   = useState(null);

  /* Restore session on mount */
  useEffect(() => {
    if (!hasToken()) {
      setLoading(false);
      return;
    }
    getMe()
      .then(setUser)
      .catch(async () => {
        // Access token likely expired — try refresh
        try {
          await refreshTokens();
          const me = await getMe();
          setUser(me);
        } catch {
          apiLogout();           // both tokens invalid, clear storage
          setUser(null);
        }
      })
      .finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (email, password) => {
    setError(null);
    try {
      await apiLogin(email, password);
      const me = await getMe();
      setUser(me);
      return me;
    } catch (err) {
      setError(err.message);
      throw err;
    }
  }, []);

  const logout = useCallback(() => {
    apiLogout();
    setUser(null);
  }, []);

  const isAdmin    = () => user?.role === "admin";
  const isAnalyst  = () => ["admin", "analyst"].includes(user?.role);
  const isLoggedIn = () => Boolean(user);

  return (
    <AuthContext.Provider value={{
      user, loading, error,
      login, logout,
      isAdmin, isAnalyst, isLoggedIn,
    }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
