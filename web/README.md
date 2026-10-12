# EcommAI web app

The shopper-facing app (Next.js, App Router, Tailwind CSS). Mobile-first: sign in, say what you want, check the checklist, watch products get checked.

## Run it

```bash
cd web
npm install
cp .env.example .env.local   # then fill in what you have
npm run dev                  # http://localhost:3000
```

To try it on a phone on the same Wi-Fi, start it on your laptop's address and open that address on the phone:

```bash
npm run dev -- --hostname 192.168.1.20
```

Google sign-in from that address only works once it's added to the allowed domains in Firebase Authentication.

Before a pull request: `npm run lint` and `npm run build`.

## Settings (`.env.local`)

| Name | What it's for |
|---|---|
| `NEXT_PUBLIC_FIREBASE_API_KEY`, `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN`, `NEXT_PUBLIC_FIREBASE_PROJECT_ID`, `NEXT_PUBLIC_FIREBASE_APP_ID` | Google sign-in through Firebase Authentication |
| `NEXT_PUBLIC_API_BASE_URL` | The agent on Cloud Run. Every request sends `Authorization: Bearer <Firebase ID token>` |
| `NEXT_PUBLIC_USE_MOCK` | `1` to use built-in sample data instead of the agent |

`NEXT_PUBLIC_` values are baked in when the app is built, so rebuild after changing them.

## Without Firebase or the agent

- **No Firebase settings:** `npm run dev` shows a "Continue in demo mode" button that skips sign-in. Production builds without them show a configuration error instead.
- **Mock mode** (`NEXT_PUBLIC_USE_MOCK=1`, or no `NEXT_PUBLIC_API_BASE_URL`): `src/lib/mock.ts` answers every API call with sample data based on the kurta example in `PLAN.md`, and plays a checking stream of about 7 seconds. Try a budget under ₹1,000 to see the "what to give up" suggestions, or "I saw someone wearing…" for a watch checklist.

## Where things are

- `src/lib/strings.ts`: every word the shopper sees. A Hindi version is another object of the same type.
- `src/lib/types.ts`: the API's data formats, kept in step with `agent/models.py`.
- `src/lib/api.ts`: the API client, including the streamed search parser (`src/lib/sse.ts`).
- `src/lib/auth.ts`: Firebase sign-in (popup, falling back to redirect) and demo mode.
- `src/app/`: the screens: `/` (sign-in or home), `/understood` ("Here's what I understood"), `/results` (live checking).
