/**
 * auth.js
 * ───────
 * Lightweight API-key authentication middleware.
 *
 * - Read routes (GET) are always public (dashboard needs them)
 * - Write routes (POST, PUT, DELETE) require a valid X-API-Key header
 * - If API_KEY env var is empty, auth is disabled (dev mode)
 *
 * The Pi sends this key via cloud_sync._build_headers() (Bearer → X-API-Key).
 * The frontend sends it via the axios default header.
 */

const API_KEY = process.env.API_KEY || "";

/**
 * Middleware: require API key on write operations.
 * GET requests pass through without auth so the dashboard works.
 */
export function requireApiKey(req, res, next) {
  // Dev mode — no key configured, skip auth entirely
  if (!API_KEY) return next();

  // Allow all reads without auth
  if (req.method === "GET" || req.method === "OPTIONS") return next();

  // Check X-API-Key header
  const provided = req.headers["x-api-key"] || req.query.apiKey || "";
  if (provided === API_KEY) return next();

  // Check Authorization: Bearer <key> (Pi sends this)
  const authHeader = req.headers["authorization"] || "";
  if (authHeader.startsWith("Bearer ") && authHeader.slice(7) === API_KEY) {
    return next();
  }

  console.warn(`[Auth] Rejected ${req.method} ${req.path} — invalid API key`);
  return res.status(403).json({ message: "Forbidden — invalid or missing API key." });
}
