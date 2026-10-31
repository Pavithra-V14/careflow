// Compares a REAL backend response to the OpenAPI schema and reports where they differ. It never returns a value from the
// response, only a path, a type word and, for a closed list, the fact that a value fell outside it. A response here can be a
// patient id or a time; the report must be safe to paste into a ticket.

type Json = Record<string, unknown>;
export interface Spec {
  components?: { schemas?: Record<string, Json> };
  paths?: Record<string, Record<string, Json>>;
}
export interface Issue {
  path: string;
  problem: string;
  /** `error`: the backend broke its own schema. `info`: something extra the schema does not mention. */
  severity: 'error' | 'info';
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DATE_TIME = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

const typeOf = (v: unknown): string => (v === null ? 'null' : Array.isArray(v) ? 'array' : typeof v === 'number' ? (Number.isInteger(v) ? 'integer' : 'number') : typeof v);

function deref(spec: Spec, node: Json): Json {
  const ref = node.$ref;
  if (typeof ref !== 'string') return node;
  const target = spec.components?.schemas?.[ref.replace('#/components/schemas/', '')];
  return target ? deref(spec, target) : {};
}

function typeMatches(declared: string, actual: string): boolean {
  return declared === actual || (declared === 'number' && actual === 'integer');
}

export function checkAgainst(spec: Spec, schemaNode: Json, value: unknown, path = '$'): Issue[] {
  const schema = deref(spec, schemaNode);

  for (const key of ['anyOf', 'oneOf'] as const) {
    const options = schema[key];
    if (Array.isArray(options)) {
      const attempts = (options as Json[]).map((o) => checkAgainst(spec, o, value, path).filter((i) => i.severity === 'error'));
      if (attempts.some((a) => a.length === 0)) return [];
      return [{ path, problem: `a ${typeOf(value)} matches none of the ${options.length} allowed shapes`, severity: 'error' }];
    }
  }
  if (Array.isArray(schema.allOf)) return (schema.allOf as Json[]).flatMap((s) => checkAgainst(spec, s, value, path));

  const issues: Issue[] = [];
  const actual = typeOf(value);

  const declared = schema.type;
  if (declared !== undefined) {
    const list = Array.isArray(declared) ? (declared as string[]) : [declared as string];
    if (!list.some((d) => typeMatches(d, actual))) return [{ path, problem: `expected ${list.join(' or ')}, got ${actual}`, severity: 'error' }];
  }

  if (Array.isArray(schema.enum) && !(schema.enum as unknown[]).includes(value)) {
    // Say that it fell outside the closed list, never which value it was.
    issues.push({ path, problem: `a ${actual} outside the closed list of ${schema.enum.length} allowed words`, severity: 'error' });
  }

  if (typeof value === 'string') {
    if (schema.format === 'uuid' && !UUID.test(value)) issues.push({ path, problem: 'a string that is not a uuid', severity: 'error' });
    if (schema.format === 'date-time' && !DATE_TIME.test(value)) issues.push({ path, problem: 'a string that is not a date-time', severity: 'error' });
    if (schema.format === 'date' && !DATE.test(value)) issues.push({ path, problem: 'a string that is not a date', severity: 'error' });
  }

  if (Array.isArray(value) && schema.items) {
    value.forEach((item, i) => issues.push(...checkAgainst(spec, schema.items as Json, item, `${path}[${i}]`)));
  }

  if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
    const obj = value as Json;
    const props = (schema.properties ?? {}) as Record<string, Json>;
    for (const name of (schema.required ?? []) as string[]) {
      if (!(name in obj)) issues.push({ path: `${path}.${name}`, problem: 'required field is missing', severity: 'error' });
    }
    for (const [name, v] of Object.entries(obj)) {
      const sub = props[name];
      if (sub) issues.push(...checkAgainst(spec, sub, v, `${path}.${name}`));
      else if (schema.additionalProperties === false) issues.push({ path: `${path}.${name}`, problem: 'field not allowed by the schema', severity: 'error' });
      else if (schema.additionalProperties && typeof schema.additionalProperties === 'object') issues.push(...checkAgainst(spec, schema.additionalProperties as Json, v, `${path}.${name}`));
      else if (Object.keys(props).length > 0) issues.push({ path: `${path}.${name}`, problem: 'field the schema does not mention', severity: 'info' });
    }
  }
  return issues;
}

/** The 200 response schema of an operation, or undefined. */
export function responseSchema(spec: Spec, method: string, pathTemplate: string): Json | undefined {
  const op = spec.paths?.[pathTemplate]?.[method.toLowerCase()] as Json | undefined;
  const ok = ((op?.responses as Json | undefined)?.['200'] ?? {}) as Json;
  return (((ok.content as Json | undefined)?.['application/json'] as Json | undefined)?.schema as Json | undefined) ?? undefined;
}

/** Finds the schema path template for a concrete request path: `/v1/staff/tasks/<uuid>/x` matches `/.../{task_id}/x`. */
export function matchTemplate(spec: Spec, concretePath: string): string | undefined {
  const parts = concretePath.split('/');
  return Object.keys(spec.paths ?? {}).find((tpl) => {
    const t = tpl.split('/');
    return t.length === parts.length && t.every((seg, i) => (seg.startsWith('{') && seg.endsWith('}') ? (parts[i] ?? '').length > 0 : seg === parts[i]));
  });
}
