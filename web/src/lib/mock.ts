// Sample agent responses for building the screens before the real agent API runs.
// Everything here stands in for server data, so it is not in the strings file.

import { addDays, todayInIndia } from "@/lib/format";
import { mustHaveLabel } from "@/lib/labels";
import type {
  BriefChange,
  BriefInput,
  BriefResponse,
  BriefUpdateResponse,
  Check,
  DoneEvent,
  Evidence,
  MustHave,
  ProductDetails,
  SearchEvent,
  ShoppingBrief,
  Status,
  Tick,
  Tradeoff,
} from "@/lib/types";

interface MockSession {
  brief: ShoppingBrief;
  questionTarget: "size" | "dial_markings" | null;
  rulesOff: string[];
  nextId: number;
}

const STORAGE_KEY = "ecommai.mock.sessions";
const CANDIDATES = 40;

const PERSONAL_RULES: MustHave[] = [
  { id: "r1", attribute: "cod", rule: "present", value: true, from_rule: true },
];

const KURTA_IDS = ["15970", "39386", "21379", "53759", "18555", "30805", "26960", "29114", "30039", "59204", "48123", "12369"];
const WATCH_IDS = ["9036", "17442", "4521", "31277", "8312", "22950", "13708", "40415", "6687", "27519", "11843", "35060"];

const KURTA_TITLES = [
  "Cotton A-line kurta with side pockets",
  "Straight cotton kurta, block print",
  "Indigo cotton kurta with pockets",
  "Rayon kurta with three-quarter sleeves",
  "Mul cotton kurta, pastel stripes",
  "Cotton kurta with yoke embroidery",
  "Printed polyester-blend kurta",
  "Chikankari cotton kurta",
  "Solid olive cotton kurta",
  "Handloom cotton kurta with pockets",
  "Flared cotton kurta, floral",
  "Khadi cotton kurta, off-white",
];
const WATCH_TITLES = [
  "Green dial steel watch, 34 mm",
  "Minimal green dial watch, mesh strap",
  "Classic steel watch, forest green dial",
  "Green sunray dial watch, 36 mm",
  "Slim steel watch, emerald dial",
  "Field watch, green dial, steel link strap",
  "Two-tone watch, green dial",
  "Small round watch, olive dial",
  "Steel bracelet watch, green face",
  "Everyday steel watch, green dial",
  "Green dial watch, brushed steel",
  "Petite steel watch, green dial",
];

type Outcome = { status: Status; prefer?: string };

const NORMAL_RUN: Outcome[] = [
  { status: "pass" },
  { status: "fail", prefer: "pockets" },
  { status: "pass" },
  { status: "unclear", prefer: "sheerness" },
  { status: "fail", prefer: "sheerness" },
  { status: "pass" },
  { status: "fail", prefer: "rule" },
  { status: "unclear", prefer: "print_intensity" },
  { status: "fail", prefer: "pockets" },
  { status: "pass" },
  { status: "fail", prefer: "pockets" },
  { status: "unclear", prefer: "sheerness" },
];

const TIGHT_BUDGET_RUN: Outcome[] = [
  { status: "fail", prefer: "pockets" },
  { status: "fail", prefer: "sheerness" },
  { status: "pass" },
  { status: "unclear", prefer: "sheerness" },
  { status: "fail", prefer: "pockets" },
  { status: "fail", prefer: "rule" },
  { status: "fail", prefer: "print_intensity" },
  { status: "unclear", prefer: "print_intensity" },
  { status: "fail", prefer: "pockets" },
  { status: "fail", prefer: "sheerness" },
  { status: "fail", prefer: "pockets" },
  { status: "fail", prefer: "fabric" },
];

const TIGHT_BUDGET_INR = 1000;

// ---------- Session storage ----------

function loadSessions(): Record<string, MockSession> {
  try {
    return JSON.parse(sessionStorage.getItem(STORAGE_KEY) ?? "{}") as Record<string, MockSession>;
  } catch {
    return {};
  }
}

function saveSession(id: string, session: MockSession) {
  const sessions = loadSessions();
  sessions[id] = session;
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
  } catch {}
}

function getSession(id: string): MockSession {
  const session = loadSessions()[id];
  if (!session) throw new MockNotFound();
  return session;
}

export class MockNotFound extends Error {}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

// ---------- Making a brief ----------

function nextThursday(): string {
  const today = todayInIndia();
  const [year, month, day] = today.split("-").map(Number);
  const weekday = new Date(Date.UTC(year, month - 1, day)).getUTCDay();
  return addDays(today, (4 - weekday + 7) % 7 || 7);
}

function budgetFrom(text: string): number | null {
  const match = /(?:under|below|upto|up to|within|max|less than)\s*(?:₹|rs\.?|inr)?\s*([\d,]{3,})/i.exec(text);
  if (!match) return null;
  const amount = Number(match[1].replace(/,/g, ""));
  return Number.isFinite(amount) && amount > 0 ? amount : null;
}

function emptyBrief(pincode: string, language: string): ShoppingBrief {
  return {
    category: "Kurtas",
    gender: "women",
    must_haves: [],
    nice_to_haves: [],
    rules_applied: [],
    budget_max_inr: null,
    deadline_date: null,
    pincode,
    size: null,
    reference_image_url: null,
    language,
    missing: [],
    question: null,
  };
}

function kurtaBrief(input: BriefInput): MockSession {
  const brief: ShoppingBrief = {
    ...emptyBrief(input.pincode, input.language),
    must_haves: [
      { id: "m1", attribute: "fabric", rule: "is", value: "cotton", from_rule: false },
      { id: "m2", attribute: "pockets", rule: "present", value: true, from_rule: false },
      { id: "m3", attribute: "sleeve_length", rule: "is", value: "three-quarter", from_rule: false },
      { id: "m4", attribute: "sheerness", rule: "is_not", value: "see-through", from_rule: false },
      { id: "m5", attribute: "print_intensity", rule: "is_not", value: "loud", from_rule: false },
    ],
    nice_to_haves: [{ attribute: "usage", value: "office" }],
    budget_max_inr: budgetFrom(input.text ?? "") ?? 1200,
    deadline_date: nextThursday(),
    missing: ["size"],
    question: "Which size do you usually wear?",
  };
  return { brief: withRules(brief, []), questionTarget: "size", rulesOff: [], nextId: 6 };
}

function photoBrief(input: BriefInput): MockSession {
  const brief: ShoppingBrief = {
    ...emptyBrief(input.pincode, input.language),
    must_haves: [{ id: "m1", attribute: "base_colour", rule: "is", value: "white", from_rule: false }],
    budget_max_inr: budgetFrom(input.text ?? ""),
    reference_image_url: typeof input.image === "string" ? input.image : null,
    missing: ["size"],
    question: "Which size do you usually wear?",
  };
  return { brief: withRules(brief, []), questionTarget: "size", rulesOff: [], nextId: 2 };
}

function watchBrief(input: BriefInput): MockSession {
  const brief: ShoppingBrief = {
    ...emptyBrief(input.pincode, input.language),
    category: "Watches",
    gender: null,
    must_haves: [
      { id: "m1", attribute: "dial_colour", rule: "is", value: "green", from_rule: false },
      { id: "m2", attribute: "strap_material", rule: "is", value: "steel", from_rule: false },
      { id: "m3", attribute: "case_size", rule: "is", value: "small", from_rule: false },
    ],
    budget_max_inr: budgetFrom(input.text ?? ""),
    question: "Numbers or lines on the dial?",
  };
  return { brief: withRules(brief, []), questionTarget: "dial_markings", rulesOff: [], nextId: 4 };
}

function ruleKey(mustHave: Pick<MustHave, "attribute" | "rule" | "value">): string {
  return `${mustHave.attribute}__${mustHave.rule}__${String(mustHave.value).toLowerCase().replace(/ /g, "-")}`;
}

/** Re-adds the shopper's personal rules that aren't switched off and that the shopper hasn't overridden. */
function withRules(brief: ShoppingBrief, rulesOff: string[]): ShoppingBrief {
  const own = brief.must_haves.filter((m) => !m.from_rule);
  const rules = PERSONAL_RULES.filter(
    (rule) => !rulesOff.includes(ruleKey(rule)) && !own.some((m) => m.attribute === rule.attribute),
  );
  return {
    ...brief,
    must_haves: [...own, ...rules],
    rules_applied: rules.map((rule) => mustHaveLabel(rule).toLowerCase()),
  };
}

export async function createBrief(input: BriefInput): Promise<BriefResponse> {
  await sleep(900);
  const text = (input.text ?? "").toLowerCase();
  const session = text.includes("watch")
    ? watchBrief(input)
    : input.image || text.includes("like this")
      ? photoBrief(input)
      : kurtaBrief(input);
  const id = `mock-${Date.now().toString(36)}`;
  saveSession(id, session);
  return { session_id: id, brief: session.brief, question: session.brief.question };
}

// ---------- Editing a brief ----------

function mustHaveFromText(text: string, id: string): MustHave {
  const lower = text.trim().toLowerCase();
  const known: [RegExp, Omit<MustHave, "id" | "from_rule">][] = [
    [/full sleeve/, { attribute: "sleeve_length", rule: "is", value: "full" }],
    [/short sleeve/, { attribute: "sleeve_length", rule: "is", value: "short" }],
    [/sleeveless/, { attribute: "sleeve_length", rule: "is", value: "sleeveless" }],
    [/no logo/, { attribute: "logo_visible", rule: "is", value: "none" }],
    [/v-?neck/, { attribute: "neckline", rule: "is", value: "v-neck" }],
    [/linen/, { attribute: "fabric", rule: "is", value: "linen" }],
  ];
  const hit = known.find(([pattern]) => pattern.test(lower));
  return hit ? { id, from_rule: false, ...hit[1] } : { id, attribute: "other", rule: "is", value: text.trim(), from_rule: false };
}

function applyChange(session: MockSession, change: BriefChange): MockSession {
  const brief = session.brief;
  switch (change.type) {
    case "remove_must_have":
      return { ...session, brief: { ...brief, must_haves: brief.must_haves.filter((m) => m.id !== change.id) } };
    case "edit_must_have":
      return {
        ...session,
        brief: {
          ...brief,
          must_haves: brief.must_haves.map((m) =>
            m.id === change.id ? { ...m, rule: change.rule, value: change.value } : m,
          ),
        },
      };
    case "add_must_have": {
      const added = mustHaveFromText(change.text, `m${session.nextId}`);
      return { ...session, nextId: session.nextId + 1, brief: { ...brief, must_haves: [...brief.must_haves, added] } };
    }
    case "set_rule": {
      const key = ruleKey(change.must_have);
      const rulesOff = change.enabled
        ? session.rulesOff.filter((k) => k !== key)
        : [...session.rulesOff.filter((k) => k !== key), key];
      return { ...session, rulesOff, brief: withRules(brief, rulesOff) };
    }
    case "remove_nice_to_have":
      return {
        ...session,
        brief: {
          ...brief,
          nice_to_haves: brief.nice_to_haves.filter(
            (n) => !(n.attribute === change.attribute && n.value === change.value),
          ),
        },
      };
    case "set_budget":
      return { ...session, brief: { ...brief, budget_max_inr: change.budget_max_inr } };
    case "set_deadline":
      return { ...session, brief: { ...brief, deadline_date: change.deadline_date } };
    case "set_pincode":
      return { ...session, brief: { ...brief, pincode: change.pincode } };
    case "set_size":
      return {
        ...session,
        brief: { ...brief, size: change.size, missing: brief.missing.filter((m) => m !== "size") },
      };
    case "answer":
      return answerQuestion(session, change.answer);
    case "text": {
      if (/cheaper/i.test(change.text) && brief.budget_max_inr) {
        const lower = Math.floor((brief.budget_max_inr * 0.8) / 50) * 50;
        return { ...session, brief: { ...brief, budget_max_inr: lower } };
      }
      return applyChange(session, { type: "add_must_have", text: change.text });
    }
  }
}

function answerQuestion(session: MockSession, answer: string): MockSession {
  const brief = { ...session.brief, question: null };
  const text = answer.trim();
  if (session.questionTarget === "size") {
    return {
      ...session,
      questionTarget: null,
      brief: {
        ...brief,
        size: text.length <= 4 ? text.toUpperCase() : text,
        missing: brief.missing.filter((m) => m !== "size"),
      },
    };
  }
  if (session.questionTarget === "dial_markings") {
    const value = /line/i.test(text) ? "lines" : /number/i.test(text) ? "numbers" : text.toLowerCase();
    const added: MustHave = { id: `m${session.nextId}`, attribute: "dial_markings", rule: "is", value, from_rule: false };
    return {
      ...session,
      questionTarget: null,
      nextId: session.nextId + 1,
      brief: { ...brief, must_haves: [...brief.must_haves, added] },
    };
  }
  return { ...session, questionTarget: null, brief };
}

export async function updateBrief(sessionId: string, change: BriefChange): Promise<BriefUpdateResponse> {
  await sleep(350);
  const next = applyChange(getSession(sessionId), change);
  saveSession(sessionId, next);
  return { brief: next.brief };
}

// ---------- Checking products ----------

const EVIDENCE: Record<string, Partial<Record<Status, Evidence>>> = {
  fabric: {
    pass: evidence("details", "The listing says 100% cotton", { quote: "Fabric: 100% Cotton" }),
    fail: evidence("details", "The listing says it's a polyester blend", { quote: "Fabric: Poly-cotton blend" }),
    unclear: evidence("details", "The listing doesn't say what it's made of"),
  },
  pockets: {
    pass: evidence("image", "Side seam pocket visible on the left", { box_2d: [520, 120, 640, 260] }),
    fail: evidence("image", "No pocket visible in any photo"),
    unclear: evidence("image", "The arms hide where a pocket would be"),
  },
  sleeve_length: {
    pass: evidence("image", "Sleeves end just below the elbow", { box_2d: [300, 60, 470, 210] }),
    fail: evidence("image", "Sleeves reach the wrist"),
    unclear: evidence("image", "Sleeves are folded in the photo"),
  },
  sheerness: {
    pass: evidence("reviews", "14 of 16 reviews say it isn't see-through", {
      quote: "Thick cotton, not see-through even in sunlight",
      agree: 14,
      disagree: 2,
    }),
    fail: evidence("reviews", "5 of 9 reviews say it's see-through in daylight", {
      quote: "Very thin, you need an inner layer",
      agree: 5,
      disagree: 4,
    }),
    unclear: evidence("reviews", "No review mentions how thick the fabric is"),
  },
  print_intensity: {
    pass: evidence("image", "Small, widely spaced print", { box_2d: [380, 330, 620, 690] }),
    fail: evidence("image", "Large, bold print across the front"),
    unclear: evidence("image", "Print size is hard to judge from this photo"),
  },
  cod: {
    pass: evidence("attribute", "Cash on delivery is available for this product"),
    fail: evidence("attribute", "This product can't be paid for on delivery"),
    unclear: evidence("attribute", "The store hasn't said whether cash on delivery is available"),
  },
};

function evidence(type: Evidence["type"], note: string, extra: Partial<Evidence> = {}): Evidence {
  return { type, note, quote: null, box_2d: null, agree: null, disagree: null, ...extra };
}

function evidenceFor(mustHave: MustHave, status: Status): Evidence {
  const known = EVIDENCE[mustHave.attribute]?.[status];
  if (known) return known;
  const label = mustHaveLabel(mustHave);
  if (status === "pass") return evidence("image", `${label}: visible in the photo`, { box_2d: [250, 250, 750, 750] });
  if (status === "fail") return evidence("image", `${label}: the photo shows otherwise`);
  return evidence("attribute", `${label}: not enough to go on`);
}

function pickTarget(mustHaves: MustHave[], outcome: Outcome, index: number): MustHave | null {
  if (mustHaves.length === 0) return null;
  const preferred =
    outcome.prefer === "rule"
      ? mustHaves.find((m) => m.from_rule)
      : mustHaves.find((m) => m.attribute === outcome.prefer && !m.from_rule);
  return preferred ?? mustHaves[index % mustHaves.length];
}

function checksFor(brief: ShoppingBrief, outcome: Outcome, index: number): Check[] {
  const target = outcome.status === "pass" ? null : pickTarget(brief.must_haves, outcome, index);
  return brief.must_haves.map((mustHave) => {
    const status: Status = target && mustHave.id === target.id ? outcome.status : "pass";
    return {
      must_have_id: mustHave.id,
      status,
      confidence: status === "unclear" ? 0.4 : 0.82 + ((index * 7 + mustHave.id.length) % 15) / 100,
      evidence: evidenceFor(mustHave, status),
    };
  });
}

function ticksFrom(brief: ShoppingBrief, checks: Check[]): Tick[] {
  return brief.must_haves.map((mustHave) => {
    const check = checks.find((c) => c.must_have_id === mustHave.id);
    return {
      must_have_id: mustHave.id,
      label: mustHaveLabel(mustHave).toLowerCase(),
      status: check?.status ?? "unclear",
      evidence: check?.evidence ?? null,
    };
  });
}

function overall(checks: Check[]): Status {
  if (checks.some((c) => c.status === "fail")) return "fail";
  return checks.every((c) => c.status === "pass") ? "pass" : "unclear";
}

/** Same wording as agent/rules.py `failure_label`, which the real rejection counts use. */
function failureLabel(mustHave: MustHave): string {
  const words = mustHave.attribute === "cod" ? "cash on delivery" : mustHave.attribute.replace(/_/g, " ");
  const text = String(mustHave.value).toLowerCase();
  let wanted: boolean | null = null;
  if (mustHave.rule === "present" || mustHave.rule === "absent") wanted = mustHave.value === (mustHave.rule === "present");
  else if ((mustHave.rule === "is" || mustHave.rule === "is_not") && (text === "yes" || text === "no")) {
    wanted = (text === "yes") === (mustHave.rule === "is");
  }
  if (wanted !== null) return wanted ? `no ${words}` : words;
  const value = String(mustHave.value);
  switch (mustHave.rule) {
    case "is":
      return `not ${value}`;
    case "is_not":
      return value;
    case "at_most":
      return `over ${value}`;
    default:
      return `under ${value}`;
  }
}

function raisedBudget(budget: number): number {
  return Math.ceil((budget * 1.2) / 50) * 50;
}

function summarise(brief: ShoppingBrief, outcomes: Status[], failures: string[], tight: boolean): DoneEvent {
  const passed = outcomes.filter((s) => s === "pass").length;
  const unclear = outcomes.filter((s) => s === "unclear").length;
  const counts = new Map<string, number>();
  const bump = (reason: string, by: number) => counts.set(reason, (counts.get(reason) ?? 0) + by);
  failures.forEach((reason) => bump(reason, 1));

  let quickDrops = CANDIDATES - outcomes.length;
  const primary = brief.must_haves.find((m) => m.attribute === "pockets") ?? brief.must_haves[0];
  const secondary = brief.must_haves.find((m) => m.attribute === "sheerness") ?? brief.must_haves[1];
  const shares: [string, number][] = [];
  if (tight && brief.budget_max_inr) shares.push(["over budget", 20]);
  if (secondary) shares.push([failureLabel(secondary), 3]);
  if (!tight && brief.budget_max_inr) shares.push(["over budget", 6]);
  if (brief.deadline_date) shares.push(["late delivery", 4]);
  for (const [reason, share] of shares) {
    const taken = Math.min(share, quickDrops);
    if (taken > 0) bump(reason, taken);
    quickDrops -= taken;
  }
  if (quickDrops > 0) bump(primary ? failureLabel(primary) : "over budget", quickDrops);

  const tradeoffs: Tradeoff[] = [];
  if (passed < 3) {
    if (brief.budget_max_inr) tradeoffs.push({ relax: "budget", to: raisedBudget(brief.budget_max_inr), matches: 5 });
    if (brief.deadline_date) tradeoffs.push({ relax: "deadline", to: addDays(brief.deadline_date, 1), matches: 3 });
    if (primary) tradeoffs.push({ relax: `must_have:${primary.attribute}`, to: null, matches: 9 });
  }

  return {
    type: "done",
    checked: CANDIDATES,
    passed,
    unclear,
    rejected: Object.fromEntries([...counts.entries()].sort((a, b) => b[1] - a[1])),
    tradeoffs,
  };
}

export async function* streamSearch(sessionId: string, signal?: AbortSignal): AsyncGenerator<SearchEvent> {
  const { brief } = getSession(sessionId);
  const tight = brief.budget_max_inr !== null && brief.budget_max_inr < TIGHT_BUDGET_INR;
  const run = tight ? TIGHT_BUDGET_RUN : NORMAL_RUN;
  const ids = brief.category === "Watches" ? WATCH_IDS : KURTA_IDS;

  await sleep(700, signal);
  yield { type: "candidates", count: CANDIDATES };

  const outcomes: Status[] = [];
  const failures: string[] = [];
  for (const [index, outcome] of run.entries()) {
    const productId = ids[index];
    yield { type: "checking", product_id: productId };
    await sleep(380, signal);
    const checks = checksFor(brief, outcome, index);
    const status = overall(checks);
    outcomes.push(status);
    const failed = brief.must_haves.find((m) => checks.some((c) => c.must_have_id === m.id && c.status === "fail"));
    if (failed) failures.push(failureLabel(failed));
    yield { type: "verified", product_id: productId, status, ticks: ticksFrom(brief, checks) };
    await sleep(120, signal);
  }

  await sleep(300, signal);
  yield summarise(brief, outcomes, failures, tight);
}

// ---------- Product details ----------

export async function getProduct(productId: string, sessionId?: string): Promise<ProductDetails> {
  await sleep(300);
  const isWatch = WATCH_IDS.includes(productId);
  const ids = isWatch ? WATCH_IDS : KURTA_IDS;
  const index = Math.max(0, ids.indexOf(productId));
  const session = sessionId ? loadSessions()[sessionId] : undefined;
  const ticks = session ? ticksFrom(session.brief, checksFor(session.brief, NORMAL_RUN[index], index)) : undefined;
  const selling = isWatch ? 1499 + index * 150 : 799 + index * 40;

  return {
    id: productId,
    title: (isWatch ? WATCH_TITLES : KURTA_TITLES)[index],
    brand: null,
    gender: isWatch ? "unisex" : "women",
    masterCategory: isWatch ? "Accessories" : "Apparel",
    subCategory: isWatch ? "Watches" : "Topwear",
    articleType: isWatch ? "Watches" : "Kurtas",
    baseColour: isWatch ? "Green" : "Blue",
    usage: isWatch ? "Casual" : "Ethnic",
    group: isWatch ? "watches" : "tops",
    description: isWatch
      ? "Round steel watch with a green dial and a steel link strap."
      : "Straight cotton kurta with three-quarter sleeves and a side seam pocket.",
    attributes: isWatch
      ? {
          dial_colour: { value: "green", confidence: 0.94, note: "Green dial", source: "gemini" },
          strap_material: { value: "steel", confidence: 0.9, note: "Link strap", source: "gemini" },
        }
      : {
          fabric: { value: "cotton", confidence: 1, note: "From the listing", source: "catalogue" },
          pockets: { value: "side", confidence: 0.86, note: "Pocket at the left seam", source: "gemini" },
          sleeve_length: { value: "three-quarter", confidence: 0.91, note: "Ends below the elbow", source: "gemini" },
        },
    catalogue_details: isWatch ? {} : { fabric: "100% Cotton" },
    needs_review: [],
    price: { mrp: Math.round(selling * 1.4), selling, demo: true },
    sizes: isWatch ? { Onesize: 6 } : { S: 4, M: 0, L: 7, XL: 2 },
    warehouse: { city: "Mumbai", pincode: "400001" },
    delivery_fee: selling >= 499 ? 0 : 49,
    cod: { available: true, fee: 29 },
    returns: { returnable: true, window_days: 14, exchange: !isWatch },
    reviews: [
      { rating: 5, text: isWatch ? "Looks smaller and neater than I expected." : "Thick cotton, not see-through.", demo: true, flagged: false },
      { rating: 4, text: isWatch ? "Strap needed one link removed." : "Pocket is deep enough for a phone.", demo: true, flagged: false },
    ],
    rating_avg: 4.3,
    rating_count: 16,
    images: null,
    demo_data: true,
    ticks,
  };
}
