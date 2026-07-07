import type { Request, Response } from 'express';
import { z } from 'zod';

import { appRouter } from '../trpc/routers/_app.js';

type ProcedureType = 'query' | 'mutation' | 'subscription';

// tRPC doesn't expose a public type for this -- `_def` is the same internal shape every
// introspection tool (trpc-panel, trpc-openapi, etc.) reaches into. Unlike trpc-ui, we never read
// zod's own `_def` here: `inputs` are the actual zod schema *instances* the router was built
// with, and we only ever call zod's own public `z.toJSONSchema()` on them -- so this doesn't
// depend on zod's internal representation the way trpc-ui's zod-v3-era introspection did.
export interface RawProcedureDef {
  type: ProcedureType;
  inputs: z.ZodType[];
}

export interface ProcedureDoc {
  path: string;
  type: ProcedureType;
  inputSchema: unknown | null;
}

export const getProcedureDocs = (procedures: Record<string, { _def: RawProcedureDef }>): ProcedureDoc[] => {
  return Object.entries(procedures)
    .map(([path, procedure]) => {
      const { type, inputs } = procedure._def;
      let inputSchema: unknown | null = null;

      if (inputs.length === 1) {
        inputSchema = z.toJSONSchema(inputs[0]);
      } else if (inputs.length > 1) {
        // Multiple chained .input() calls -- not used anywhere in this router today, but handle
        // it rather than silently dropping inputs if that ever changes.
        inputSchema = { allOf: inputs.map(input => z.toJSONSchema(input)) };
      }

      return { path, type, inputSchema };
    })
    .sort((a, b) => a.path.localeCompare(b.path));
};

const TYPE_COLOR: Record<ProcedureType, string> = {
  query: '#2563eb',
  mutation: '#d97706',
  subscription: '#7c3aed'
};

const escapeHtml = (value: string): string =>
  value.replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]!);

const renderProcedure = (doc: ProcedureDoc): string => {
  const color = TYPE_COLOR[doc.type];
  const body = doc.inputSchema
    ? `<pre>${escapeHtml(JSON.stringify(doc.inputSchema, null, 2))}</pre>`
    : `<p class="no-input">No input</p>`;

  return `
    <div class="procedure" id="${escapeHtml(doc.path)}">
      <h3><span class="badge" style="background:${color}">${doc.type}</span>${escapeHtml(doc.path)}</h3>
      ${body}
    </div>`;
};

export const renderPage = (docs: ProcedureDoc[]): string => {
  const groups = new Map<string, ProcedureDoc[]>();
  for (const doc of docs) {
    const group = doc.path.split('.')[0];
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group)!.push(doc);
  }

  const nav = [...groups.keys()]
    .map(group => `<li><a href="#${escapeHtml(group)}">${escapeHtml(group)}</a></li>`)
    .join('');

  const sections = [...groups.entries()]
    .map(
      ([group, procedures]) => `
      <section id="${escapeHtml(group)}">
        <h2>${escapeHtml(group)}</h2>
        ${procedures.map(renderProcedure).join('')}
      </section>`
    )
    .join('');

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>API schema -- trading-system-backend</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; margin: 0; display: flex; }
  nav { position: sticky; top: 0; height: 100vh; overflow-y: auto; padding: 1.5rem; border-right: 1px solid #8884; min-width: 12rem; }
  nav ul { list-style: none; padding: 0; margin: 0; }
  nav a { text-decoration: none; color: inherit; opacity: 0.75; }
  nav a:hover { opacity: 1; }
  main { padding: 1.5rem 2rem; flex: 1; max-width: 60rem; }
  h1 { font-size: 1.1rem; }
  h2 { border-bottom: 1px solid #8884; padding-bottom: 0.25rem; margin-top: 2.5rem; }
  h3 { font-size: 0.95rem; display: flex; align-items: center; gap: 0.5rem; }
  .badge { color: white; font-size: 0.7rem; padding: 0.15rem 0.5rem; border-radius: 0.3rem; text-transform: uppercase; }
  .procedure { margin: 1.25rem 0; padding: 0.75rem 1rem; border: 1px solid #8884; border-radius: 0.5rem; }
  .no-input { opacity: 0.6; margin: 0.5rem 0 0; }
  pre { overflow-x: auto; font-size: 0.8rem; margin: 0.5rem 0 0; }
</style>
</head>
<body>
<nav><h1>Routers</h1><ul>${nav}</ul></nav>
<main>
<h1>trading-system-backend -- API schema</h1>
<p>Generated live from the running tRPC router. Input schemas via zod's own <code>toJSONSchema()</code>; no output schemas (procedures don't declare <code>.output()</code>), no subscriptions payload shape.</p>
${sections}
</main>
</body>
</html>`;
};

export const apiDocsHandler = (_req: Request, res: Response): void => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const procedures = (appRouter as any)._def.procedures as Record<string, { _def: RawProcedureDef }>;
  res.type('html').send(renderPage(getProcedureDocs(procedures)));
};
