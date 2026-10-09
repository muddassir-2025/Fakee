/**
 * Auth state for the site.
 *
 * Wraps the Neon Auth client in a small context so pages can ask "who is signed
 * in" without each of them re-reading the session. The session is re-read when
 * the tab regains focus (returning from the Google OAuth redirect, or after
 * signing in elsewhere) which keeps the header honest without polling.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  type AuthUser,
  accessToken,
  authAvailable,
  isAdmin,
  readSession,
  signInWithGoogle,
  signOutNow,
} from "./auth";
import { handOffSession, requestedExtensionId } from "./extensionAuth";

interface AuthContextValue {
  user: AuthUser | null;
  loading: boolean;
  error: string | null;
  /** False when this deployment has no Neon Auth project configured. */
  available: boolean;
  admin: boolean;
  /** True once a session has been handed to the extension in this tab. */
  handedOff: boolean;
  signIn: () => Promise<void>;
  signOut: () => Promise<void>;
  token: () => Promise<string | null>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(authAvailable);
  const [error, setError] = useState<string | null>(null);
  const [handedOff, setHandedOff] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    if (!authAvailable) {
      setLoading(false);
      return;
    }
    const next = await readSession();
    if (!mounted.current) return;
    setUser(next);
    setLoading(false);
  }, []);

  useEffect(() => {
    void refresh();

    const onFocus = () => void refresh();
    const onVisibility = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [refresh]);

  // A page opened by the extension (`?ext=<extension id>`) is how the extension
  // gets a token after sign-in, so it can post reports with the user's identity.
  useEffect(() => {
    if (!user) return;
    const extensionId = requestedExtensionId();
    if (!extensionId) return;
    let cancelled = false;
    void (async () => {
      const token = await accessToken();
      if (!token || cancelled) return;
      const ok = await handOffSession(extensionId, token, user);
      if (!cancelled && ok) setHandedOff(true);
    })();
    return () => {
      cancelled = true;
    };
  }, [user]);

  const signIn = useCallback(async () => {
    setError(null);
    try {
      await signInWithGoogle();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const signOut = useCallback(async () => {
    setError(null);
    await signOutNow();
    if (mounted.current) {
      setUser(null);
      setHandedOff(false);
    }
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      loading,
      error,
      available: authAvailable,
      admin: isAdmin(user),
      handedOff,
      signIn,
      signOut,
      token: accessToken,
      refresh,
    }),
    [user, loading, error, handedOff, signIn, signOut, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>.");
  return context;
}
