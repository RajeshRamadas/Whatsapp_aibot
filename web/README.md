# Admin web app (T3.1)

Next.js 15, TypeScript strict, Tailwind 4 and Radix-based components. English UI copy is in `src/messages/en.json`.

Run `npm install && npm run dev` from `web/`. The gallery is at `/dev/components` in development only. Run `npm run lint`, `npm run typecheck`, `npm test`, and `npm run build`.

With FastAPI running on port 8000, run `npm run gen:api` to generate `src/api/schema.d.ts` from the live OpenAPI schema. Regenerate when API changes. The app currently has no API-consuming screens.
