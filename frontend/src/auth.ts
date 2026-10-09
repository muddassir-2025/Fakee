/**
 * Neon Auth (Managed Better Auth) client.
 *
 * Sign-in is delegated to Neon Auth over Google OAuth. Because the API lives on
 * a different origin than the auth service, the client is configured to send the
 * session cookie cross-origin (`credentials: "include"`), which is what lets
 * `token()` mint the short-lived JWT the backend verifies.
 *
 * The whole site still works without any of this: when `VITE_NEON_AUTH_URL` is
 * unset the helpers below report "auth unavailable" and the UI explains that
 * reporting is disabled rather than failing silently.
 */
import { createAuthClient } from "@neondatabase/neon-js/auth";
import { BetterAuthVanillaAdapter } from "@neondatabase/neon-js/auth/vanilla";

export const AUTH_URL = ((import.meta.env.VITE_NEON_AUTH_URL as string | undefined) ?? "").trim();

/** False when the deployment has no Neon Auth project configured. */
export const authAvailable = AUTH_URL.length > 0;

export interface AuthUser {
  id: string;
  email?: string | null;
  name?: string | null;
  image?: string | null;
  emailVerified?: boolean;
  /** Present on the session payload the SDK returns; verified server-side. */
  token?: string;
}

export const authClient = authAvailable
  ? createAuthClient(AUTH_URL, {
      // The SPA and the auth service live on different origins, so the session
      // cookie has to ride along explicitly or `token()` comes back empty.
      adapter: BetterAuthVanillaAdapter({ fetchOptions: { credentials: "include" } }),
    })
  : null;

/** The address that may open the admin dashboard. Kept in step with ADMIN_EMAILS. */
export const ADMIN_EMAIL = (
  (import.meta.env.VITE_ADMIN_EMAIL as string | undefined) ?? "studymuddassir@gmail.com"
)
  .trim()
  .toLowerCase();

export function isAdmin(user: AuthUser | null | undefined): boolean {
  const email = (user?.email ?? "").trim().toLowerCase();
  return Boolean(email) && email === ADMIN_EMAIL;
}

/** Read the current session, or null when signed out / unconfigured. */
export async function readSession(): Promise<AuthUser | null> {
  if (!authClient) return null;
  try {
    const result = await authClient.getSession();
    const user = (result?.data as { user?: AuthUser } | null)?.user;
    return user ?? null;
  } catch {
    // A network hiccup must not look like "signed in".
    return null;
  }
}

/** Start the Google OAuth flow, returning here afterwards. */
export async function signInWithGoogle(): Promise<void> {
  if (!authClient) throw new Error("Sign-in is not configured for this deployment.");
  const callbackURL = window.location.href;
  const result = await authClient.signIn.social({ provider: "google", callbackURL });
  const error = (result as { error?: { message?: string } } | null)?.error;
  if (error) throw new Error(error.message || "Could not start Google sign-in.");
}

export async function signOutNow(): Promise<void> {
  if (!authClient) return;
  await authClient.signOut();
}

/**
 * A fresh access token for API calls.
 *
 * Managed Better Auth tokens live for 15 minutes, so this is called per request
 * rather than cached — minting one from an existing session is local work, not a
 * round trip that needs economising.
 *
 * The JWT is read defensively: the documented shape is `data.token`, but the
 * shipped SDK (0.7.0-beta) returns it nested as `data.session.token`. The
 * session itself also carries the token (injected from the `set-auth-jwt`
 * header), which is the fallback.
 */
export async function accessToken(): Promise<string | null> {
  if (!authClient) return null;

  type TokenPayload = { token?: string; session?: { token?: string } | null } | null;

  try {
    const result = await authClient.token();
    const data = (result as { data?: TokenPayload } | null)?.data;
    const token = data?.token ?? data?.session?.token;
    if (token) return token;
  } catch {
    // Fall through to the session, which usually still has a live token.
  }

  try {
    const result = await authClient.getSession();
    const data = (result as { data?: { session?: { token?: string } | null } | null } | null)?.data;
    return data?.session?.token ?? null;
  } catch {
    return null;
  }
}
