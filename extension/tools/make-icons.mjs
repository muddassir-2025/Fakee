/**
 * Generates the extension's PNG icons, with no image dependencies.
 *
 *   node tools/make-icons.mjs
 *
 * The mark mirrors the website: a lens with a bar through it, in the shared
 * signal-orange on a rounded tile. Everything is rasterised here so the icon set
 * can be regenerated from one source of truth instead of a checked-in binary
 * that nobody can edit.
 */

import { deflateSync } from "node:zlib";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = join(HERE, "..", "icons");

const ORANGE = [194, 65, 12];
const WHITE = [255, 255, 255];

/* ----------------------------------------------------------------- PNG --- */

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(buf) {
  let c = 0xffffffff;
  for (const byte of buf) c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length, 0);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body), 0);
  return Buffer.concat([length, body, crc]);
}

function encodePng(width, height, rgba) {
  const raw = Buffer.alloc(height * (width * 4 + 1));
  let offset = 0;
  for (let y = 0; y < height; y += 1) {
    raw[offset] = 0; // filter: none
    offset += 1;
    for (let x = 0; x < width; x += 1) {
      const i = (y * width + x) * 4;
      raw[offset] = rgba[i];
      raw[offset + 1] = rgba[i + 1];
      raw[offset + 2] = rgba[i + 2];
      raw[offset + 3] = rgba[i + 3];
      offset += 4;
    }
  }

  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // colour type: RGBA
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

/* --------------------------------------------------------------- shapes --- */

/** Normalised point-in-rounded-rectangle test (coordinates 0..1). */
function inRoundedRect(x, y, radius) {
  const cx = Math.min(Math.max(x, radius), 1 - radius);
  const cy = Math.min(Math.max(y, radius), 1 - radius);
  const dx = x - cx;
  const dy = y - cy;
  return dx * dx + dy * dy <= radius * radius;
}

function distanceToSegment(px, py, ax, ay, bx, by) {
  const vx = bx - ax;
  const vy = by - ay;
  const wx = px - ax;
  const wy = py - ay;
  const len2 = vx * vx + vy * vy;
  const t = len2 === 0 ? 0 : Math.min(1, Math.max(0, (wx * vx + wy * vy) / len2));
  const dx = px - (ax + t * vx);
  const dy = py - (ay + t * vy);
  return Math.hypot(dx, dy);
}

function onStroke(px, py, ax, ay, bx, by, half) {
  return distanceToSegment(px, py, ax, ay, bx, by) <= half;
}

/** Where the mark (lens + bar + handle) covers a normalised point. */
function onMark(x, y, size) {
  const cx = 0.42;
  const cy = 0.42;
  const radius = 0.245;
  const half = Math.max(0.038, 1.6 / size);
  const inRing = Math.abs(Math.hypot(x - cx, y - cy) - radius) <= half;
  const onBar = onStroke(x, y, 0.27, cy, 0.57, cy, Math.max(0.034, 1.5 / size));
  const onHandle = onStroke(x, y, cx + radius * 0.72, cy + radius * 0.72, 0.79, 0.79, half);
  return inRing || onBar || onHandle;
}

/* ---------------------------------------------------------------- render --- */

function renderIcon(size) {
  const supersample = 4;
  const radius = 0.235;
  const rgba = Buffer.alloc(size * size * 4);

  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      // Premultiplied accumulation keeps edges clean without darker fringes.
      let pr = 0;
      let pg = 0;
      let pb = 0;
      let pa = 0;

      for (let sy = 0; sy < supersample; sy += 1) {
        for (let sx = 0; sx < supersample; sx += 1) {
          const px = (x + (sx + 0.5) / supersample) / size;
          const py = (y + (sy + 0.5) / supersample) / size;
          if (!inRoundedRect(px, py, radius)) continue;
          const colour = onMark(px, py, size) ? WHITE : ORANGE;
          pr += colour[0];
          pg += colour[1];
          pb += colour[2];
          pa += 255;
        }
      }

      const samples = supersample * supersample;
      const alpha = pa / samples;
      const i = (y * size + x) * 4;
      if (pa === 0) {
        rgba[i] = 0;
        rgba[i + 1] = 0;
        rgba[i + 2] = 0;
        rgba[i + 3] = 0;
      } else {
        // Un-premultiply back to straight alpha for PNG.
        const weight = pa / 255;
        rgba[i] = Math.round(pr / weight);
        rgba[i + 1] = Math.round(pg / weight);
        rgba[i + 2] = Math.round(pb / weight);
        rgba[i + 3] = Math.round(alpha);
      }
    }
  }

  return encodePng(size, size, rgba);
}

mkdirSync(OUT_DIR, { recursive: true });
for (const size of [16, 32, 48, 128]) {
  const file = join(OUT_DIR, `icon${size}.png`);
  writeFileSync(file, renderIcon(size));
  console.log(`wrote ${file}`);
}
