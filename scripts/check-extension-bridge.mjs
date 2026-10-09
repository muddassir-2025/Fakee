#!/usr/bin/env node
/**
 * Guards the website <-> extension seam.
 *
 * The two halves live in different languages and are never imported together,
 * so a rename on one side would fail silently at runtime. This asserts they
 * agree on the Port name, the message types each sends, and the origins Chrome
 * is told are allowed to connect.
 *
 * Usage: node scripts/check-extension-bridge.mjs
 */

import { readFileSync } from "node:fs";

const read = (relative) => readFileSync(new URL(relative, import.meta.url), "utf8");

const background = read("../extension/background.js");
const site = read("../frontend/src/extensionBridge.ts");
const manifest = JSON.parse(read("../extension/manifest.json"));

const checks = [];
const check = (name, pass, detail = "") => checks.push({ name, pass, detail });

// ---- Port name --------------------------------------------------------------
const extPort = /port\.name !== "([^"]+)"/.exec(background)?.[1];
const sitePort = /PORT_NAME = "([^"]+)"/.exec(site)?.[1];
check("both sides use the same Port name", extPort && extPort === sitePort, `${extPort} vs ${sitePort}`);

// ---- Messages the site sends, the extension must handle ---------------------
for (const type of ["PING", "RUN"]) {
  const siteSends = new RegExp(`postMessage\\(\\{[^}]*type: "${type}"`).test(site);
  const extHandles = background.includes(`message?.type === "${type}"`);
  check(`site sends ${type} and the extension handles it`, siteSends && extHandles, `send=${siteSends} handle=${extHandles}`);
}

// ---- Messages the extension sends, the site must handle --------------------
// PONG comes from background.js; the rest are emitted by the offscreen crawler
// and relayed through the worker.
const offscreen = read("../extension/offscreen.js");
for (const type of ["PONG", "PROGRESS", "RESULT", "ERROR"]) {
  const extSends =
    background.includes(`type: "${type}"`) || offscreen.includes(`type: "${type}"`);
  // The site may write `message?.type` (optional chaining); accept both forms.
  const siteHandles = new RegExp(`message\\??\\.type === "${type}"`).test(site);
  check(`extension sends ${type} and the site handles it`, extSends && siteHandles, `send=${extSends} handle=${siteHandles}`);
}

// ---- Allowed origins --------------------------------------------------------
const matches = manifest.externally_connectable?.matches ?? [];
check(
  "manifest allows vercel deployments to connect",
  matches.some((m) => m.includes("vercel.app")),
  matches.join(", "),
);
check(
  "manifest allows local development to connect",
  matches.some((m) => m.includes("localhost")),
  matches.join(", "),
);

// The background re-checks the origin, so its pattern must cover the same hosts.
check(
  "background origin check covers vercel.app",
  background.includes("vercel\\.app"),
);
check(
  "background origin check covers localhost",
  background.includes("localhost"),
);

// ---- The site can derive the id from a real store URL -----------------------
const storeUrlCheck = /abcdefghijklmnop/.test(site) === false; // sanity: no test id leaked
check("no test extension id left in the bridge source", storeUrlCheck);

let failed = 0;
for (const c of checks) {
  if (!c.pass) failed += 1;
  console.log(`${c.pass ? "PASS" : "FAIL"}  ${c.name}${c.pass ? "" : `  (${c.detail})`}`);
}

console.log(`\n${failed === 0 ? "PASS" : `FAIL (${failed})`} — ${checks.length} contract checks`);
process.exit(failed === 0 ? 0 : 1);
