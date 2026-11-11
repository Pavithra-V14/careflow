/**
 * The reasons the clinic allows an erasure for. NO ROUTE returns this list yet (docs/frontend-backend-requests.md): the backend keeps it
 * as a required setting and answers 503 to a request while it is unset. Until a route exists this returns null and the request form
 * shows the calm "not set up" state and cannot be submitted. The console keeps no list of its own: which reasons a clinic may erase for
 * is a privacy and legal decision. When the route arrives, make this a query; the form works unchanged.
 */
export function useErasureReasons(): readonly string[] | null {
  return null;
}
