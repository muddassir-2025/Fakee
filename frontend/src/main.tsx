import React from "react";
import ReactDOM from "react-dom/client";
import { Analytics } from "@vercel/analytics/react";
import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { AuthProvider } from "./useAuth";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <ErrorBoundary>
      <AuthProvider>
        <App />
        {/*
          Vercel Web Analytics: cookieless, aggregate page-view counting. It
          injects `/_vercel/insights/script.js`, which only Vercel serves, so on
          another host (or before Web Analytics is enabled for the project) that
          request 404s and nothing is recorded — the failed load is expected and
          harmless there. Mounted as a sibling of <App /> so it stays alive
          across hash-route changes.
        */}
        <Analytics />
      </AuthProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
