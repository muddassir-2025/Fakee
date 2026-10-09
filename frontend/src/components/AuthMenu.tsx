/**
 * Header account control.
 *
 * Signed out it is a single "Sign in" pill that starts the Google flow; signed
 * in it is the user's initial (linking to their reports), an Admin link for the
 * administrator, and a sign-out button. Deliberately no dropdown: this header
 * has to stay usable at 320px, and a menu that needs outside-click handling is a
 * lot of state for three links.
 */
import { useAuth } from "../useAuth";

export function AuthMenu() {
  const { user, loading, available, admin, signIn, signOut } = useAuth();

  if (!available) return null;

  if (loading) {
    return <span className="shrink-0 text-xs text-faint">…</span>;
  }

  if (!user) {
    return (
      <button
        type="button"
        onClick={() => void signIn()}
        className="shrink-0 rounded-full border border-line-strong px-3.5 py-2 text-sm font-medium text-ink transition-colors hover:bg-paper-deep sm:px-4"
      >
        Sign in
      </button>
    );
  }

  const initial = (user.name || user.email || "?").trim().slice(0, 1).toUpperCase();

  return (
    <div className="flex shrink-0 items-center gap-2">
      {admin && (
        <a
          href="#/admin"
          className="rounded-full border border-line-strong px-3.5 py-2 text-sm font-medium text-ink transition-colors hover:bg-paper-deep"
        >
          Admin
        </a>
      )}
      <a
        href="#/profile"
        title={`${user.email ?? "Signed in"} — my reports`}
        className="flex h-9 w-9 items-center justify-center rounded-full bg-signal-soft font-display text-sm font-semibold text-signal transition-colors hover:bg-signal/20"
      >
        <span className="sr-only">My reports</span>
        <span aria-hidden="true">{initial}</span>
      </a>
      <button
        type="button"
        onClick={() => void signOut()}
        className="hidden text-sm text-muted transition-colors hover:text-ink md:inline"
      >
        Sign out
      </button>
    </div>
  );
}
