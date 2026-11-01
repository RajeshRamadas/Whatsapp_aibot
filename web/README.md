# Admin web app (T3.1)

Next.js 15, TypeScript strict, Tailwind 4 and Radix-based components. English UI copy is in `src/messages/en.json`.

Run `npm install && npm run dev` from `web/`. The gallery is at `/dev/components` in development only. Run `npm run lint`, `npm run typecheck`, `npm test`, and `npm run build`.

With FastAPI running on port 8000, run `npm run gen:api` to generate `src/api/schema.d.ts` from the live OpenAPI schema. Regenerate when API changes. The app currently has no API-consuming screens.

T3.3 adds a role-filtered desktop sidebar and mobile bottom navigation, workspace switcher, onboarding checklist and placeholder sections. The checklist reads `/api/v1/onboarding` with `X-Workspace-Id`. Knowledge and WhatsApp steps are computed from tenant data; test-bot and go-live remain incomplete until T3.5 implements persistent state. The needs-attention panel is a placeholder until M4.
