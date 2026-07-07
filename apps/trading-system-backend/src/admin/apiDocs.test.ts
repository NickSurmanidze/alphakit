import { describe, expect, it } from 'vitest';
import { z } from 'zod';

import { getProcedureDocs, renderPage } from './apiDocs.js';

// A small hand-built router shape rather than the real appRouter -- importing the real one pulls
// in env.ts, which requires real environment variables at import time (MONGO_URI etc.), making
// tests fragile outside a machine with a populated .env. getProcedureDocs/renderPage only need
// the `_def.procedures` shape, so a fake one exercises the same logic without that dependency.
const fakeProcedures = {
  'widgets.list': { _def: { type: 'query' as const, inputs: [] } },
  'widgets.create': {
    _def: {
      type: 'mutation' as const,
      inputs: [z.object({ name: z.string().min(1), quantity: z.number().int().positive().default(1) })]
    }
  },
  'widgets.onUpdate': { _def: { type: 'subscription' as const, inputs: [z.object({ id: z.string() })] } }
};

describe('getProcedureDocs', () => {
  it('returns one doc per procedure, sorted by path', () => {
    const docs = getProcedureDocs(fakeProcedures);

    expect(docs.map(d => d.path)).toEqual(['widgets.create', 'widgets.list', 'widgets.onUpdate']);
  });

  it('carries the procedure type through', () => {
    const docs = getProcedureDocs(fakeProcedures);

    expect(docs.find(d => d.path === 'widgets.create')?.type).toBe('mutation');
    expect(docs.find(d => d.path === 'widgets.onUpdate')?.type).toBe('subscription');
  });

  it('is null for a procedure with no input', () => {
    const docs = getProcedureDocs(fakeProcedures);

    expect(docs.find(d => d.path === 'widgets.list')?.inputSchema).toBeNull();
  });

  it('renders a real JSON schema (fields, types, defaults) for a procedure with input', () => {
    const docs = getProcedureDocs(fakeProcedures);

    const schema = docs.find(d => d.path === 'widgets.create')?.inputSchema as {
      properties: Record<string, unknown>;
      required: string[];
    };

    expect(schema.properties).toHaveProperty('name');
    expect(schema.properties).toHaveProperty('quantity');
    expect(schema.properties.quantity).toMatchObject({ default: 1 });
    // zod v4's toJSONSchema() keeps a defaulted field in `required` (it's always present after
    // parsing, default or not) -- only genuinely optional fields are excluded.
    expect(schema.required).toEqual(['name', 'quantity']);
  });

  it('merges multiple chained .input() calls into an allOf instead of dropping them', () => {
    const docs = getProcedureDocs({
      'widgets.multiInput': {
        _def: {
          type: 'mutation',
          inputs: [z.object({ a: z.string() }), z.object({ b: z.string() })]
        }
      }
    });

    const schema = docs[0].inputSchema as { allOf: unknown[] };
    expect(schema.allOf).toHaveLength(2);
  });
});

describe('renderPage', () => {
  it('groups procedures by the router prefix before the first dot', () => {
    const html = renderPage(getProcedureDocs(fakeProcedures));

    expect(html).toContain('id="widgets"');
    expect(html).toContain('widgets.list');
    expect(html).toContain('widgets.create');
  });

  it('escapes schema content so it cannot break out of the <pre> block', () => {
    const docs = getProcedureDocs({
      'widgets.xss': {
        _def: { type: 'mutation', inputs: [z.object({ name: z.string().describe('</pre><script>x</script>') })] }
      }
    });

    const html = renderPage(docs);

    expect(html).not.toContain('<script>x</script>');
    expect(html).toContain('&lt;script&gt;');
  });

  it('shows "No input" for a query with no input schema', () => {
    const html = renderPage(getProcedureDocs(fakeProcedures));

    expect(html).toContain('No input');
  });
});
