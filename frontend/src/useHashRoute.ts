/**
 * Minimal hash router.
 *
 * The landing page already uses `#problem`/`#check` anchors for its own
 * sections, so only hashes that start with `#/` are treated as pages. That
 * keeps deep links (`/profile`, `/admin`) working on any static host without a
 * rewrite rule, and never collides with an in-page anchor.
 */
import { useEffect, useState } from "react";

export type Route = "home" | "profile" | "admin";

const ROUTES: Record<string, Route> = {
  "": "home",
  "/": "home",
  "/profile": "profile",
  "/admin": "admin",
};

export function routeFromHash(hash: string): Route {
  const raw = (hash || "").trim();
  if (raw === "#" || !raw.startsWith("#/")) return "home";
  const path = raw.slice(1).split("?")[0].replace(/\/+$/, "") || "/";
  return ROUTES[path] ?? "home";
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => routeFromHash(window.location.hash));

  useEffect(() => {
    const onHashChange = () => setRoute(routeFromHash(window.location.hash));
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  return route;
}

/** Navigate to a page, keeping any `?ext=` query intact for the handoff. */
export function navigate(route: Route): void {
  const path = route === "home" ? "/" : `/${route}`;
  window.location.hash = `#${path}`;
}

/** Scroll to a landing-page section, going home first when needed. */
export function goToSection(id: string): void {
  if (routeFromHash(window.location.hash) !== "home") {
    window.location.hash = "#/";
    // Wait for the landing page to render before looking the section up.
    window.setTimeout(() => document.getElementById(id)?.scrollIntoView({ behavior: "smooth" }), 60);
    return;
  }
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}
