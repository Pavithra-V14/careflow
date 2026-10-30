// Content Security Policy for the PRODUCTION build only (dev needs inline HMR scripts and a websocket).
// One source: the same string goes into a <meta> tag in every built HTML page and into dist/csp-policy.txt,
// for a host that can send it as a header (a header can also carry frame-ancestors and report-uri, which
// a <meta> tag cannot).

export interface CspInput {
  supabaseUrl: string;
  apiBaseUrl: string;
}

/** The origin of a URL, or null when it is empty or not an http(s) URL. Never throws. */
export function originOf(value: string): string | null {
  try {
    const u = new URL(value);
    return u.protocol === 'https:' || u.protocol === 'http:' ? u.origin : null;
  } catch {
    return null;
  }
}

export function buildCsp({ supabaseUrl, apiBaseUrl }: CspInput): string {
  const connect = ["'self'", originOf(supabaseUrl), originOf(apiBaseUrl)].filter((o): o is string => o !== null);
  const directives: Record<string, string[]> = {
    'default-src': ["'none'"],
    'script-src': ["'self'"],
    'style-src': ["'self'"],
    // data: is required for the authenticator QR code, which supabase-js returns as a data:image/svg+xml URI.
    'img-src': ["'self'", 'data:'],
    'font-src': ["'self'"],
    'connect-src': [...new Set(connect)],
    'base-uri': ["'self'"],
    'form-action': ["'self'"],
    'object-src': ["'none'"],
  };
  return Object.entries(directives)
    .map(([name, values]) => `${name} ${values.join(' ')}`)
    .join('; ');
}

/** Adds the policy as a <meta> tag as the first element of <head> (before any script or style). */
export function injectCspMeta(html: string, policy: string): string {
  const tag = `<meta http-equiv="Content-Security-Policy" content="${policy}" />`;
  return html.replace(/<head>/i, `<head>\n    ${tag}`);
}
