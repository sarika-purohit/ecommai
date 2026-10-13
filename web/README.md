# EcommAI web app

The shopper-facing app (Next.js, App Router, Tailwind CSS). Mobile-first: sign in, say what you want, check the checklist, watch products get checked, open the proof, buy on the (demo) store, and manage your rules.

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

Before a pull request: `npm run lint`, `npx tsc --noEmit` and `npm run build`.

## Settings (`.env.local`)

| Name | What it's for |
|---|---|
| `NEXT_PUBLIC_FIREBASE_API_KEY`, `NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN`, `NEXT_PUBLIC_FIREBASE_PROJECT_ID`, `NEXT_PUBLIC_FIREBASE_APP_ID` | Google sign-in through Firebase Authentication |
| `NEXT_PUBLIC_API_BASE_URL` | The agent on Cloud Run. Every request sends `Authorization: Bearer <Firebase ID token>` |
| `NEXT_PUBLIC_USE_MOCK` | `1` to use built-in sample data instead of the agent |

`NEXT_PUBLIC_` values are baked in when the app is built, so rebuild after changing them.

## Screens

| Route | Screen |
|---|---|
| `/` | Sign-in, then home: the request box, photo and example chips |
| `/understood` | "Here's what I understood": the checklist as editable chips, locked rule chips, one question |
| `/results` | Live checking (thumbnails stamped pass, fail or not sure as checks land), then the results: cards with total price, delivery date and the tick-list; tap a tick for its proof. Below: "what to give up" (one tap edits the checklist and searches again), `cheaper` / `more formal` / `different colour`, the ones we couldn't fully confirm, and why others were rejected |
| `/product/<id>` | Every photo size, every tick with proof, reviews (flagged ones hidden behind "hidden: suspicious"), size picker, "Buy on store" |
| `/checkout?product=&size=` | The demo store's checkout. Clearly labelled; no payment, nothing delivered |
| `/profile` | Pincode, my rules (same chip words as the checklist), what we've learned with a clear button, "Delete my data", sign out |

Photo proof scales `box_2d` (`[ymin, xmin, ymax, xmax]` on 0-1000) onto the photo as shown and dims everything around it. A missing or invalid box shows the whole photo with a note.

## Without Firebase or the agent

- **No Firebase settings:** `npm run dev` shows a "Continue in demo mode" button that skips sign-in. Production builds without them show a configuration error instead.
- **Mock mode** (`NEXT_PUBLIC_USE_MOCK=1`, or no `NEXT_PUBLIC_API_BASE_URL`): `src/lib/mock.ts` answers every endpoint offline, using 24 real catalogue products in `src/lib/mock-catalog.ts` (12 kurtas, 12 watches, with real photos, shop data, reviews and careful-check boxes). Checks follow each product's saved details; ranking and "what to give up" follow `agent/rank.py` and `agent/tradeoffs.py`, so the counts on a trade-off card match what one tap gives. It's lazy-loaded, so real builds don't ship it. Things to try:
  - "Cotton kurta with pockets under ₹1,200": five matches, one we couldn't confirm, a near-duplicate left out. Tap "Pockets" on the white Fabindia kurta for the photo proof.
  - Add "three-quarter sleeves": nothing matches, so the "what to give up" card offers dropping sleeves.
  - "I saw someone wearing…" (green dial, steel strap): nothing matches; dropping the dial colour gives eight.
  - Add "not see-through": every kurta is unclear, with review proof.
  - Mention a brand ("Fabindia kurta…") to see "We can't check these yet".
  - Put "test error" in the request: the first search stops halfway with a retryable error.
  - Rules saved on `/profile` join the next checklist as locked chips (stored in this browser only).

## Where things are

- `src/lib/strings.ts`: every word the shopper sees. A Hindi version is another object of the same type.
- `src/lib/types.ts`: the API's data formats, kept in step with `agent/models.py`, `agent/rank.py`, `agent/tradeoffs.py` and `agent/cards.py`.
- `src/lib/api.ts`: the API client, including the streamed search parser (`src/lib/sse.ts`).
- `src/lib/session.ts`: the current search, its finished results and likes, kept in session storage.
- `src/lib/vocabulary.ts`: the detail vocabulary for the rule editor (mirrors `data_prep/schemas.py`).
- `src/lib/auth.ts`: Firebase sign-in (popup, falling back to redirect) and demo mode.
- `src/components/proof/TickList.tsx`: tick tags and their proof, shared by result cards and the product page.
