// Every piece of text the shopper sees. A Hindi version is another object of type Strings.

interface AttributeWords {
  /** What the detail is called on its own, e.g. "Sleeves". */
  name: string;
  /** Added after the value: "three-quarter" + "sleeves". */
  noun?: string;
  /** Used when the must-have is about the detail being there or not: "pockets", "no pockets". */
  presence?: string;
  /** Replaces the usual "value noun" wording. */
  phrase?: (value: string) => string;
  /** Replaces the usual "no value noun" wording. */
  negated?: (value: string) => string;
}

const en = {
  locale: "en-IN",
  /** Sent to the agent as the shopper's language. */
  language: "en",
  appName: "EcommAI",

  meta: {
    title: "EcommAI",
    pageTitle: (page: string) => `${page} · EcommAI`,
    description:
      "Say what it must have. EcommAI checks every product against your list and shows you the proof.",
  },

  common: {
    cancel: "Cancel",
    save: "Save",
    tryAgain: "Try again",
    loading: "Loading",
  },

  signIn: {
    title: "Shop by what it must have.",
    lede: "Tell us what you need. We check every product against your list and only show the ones that have it all, with proof.",
    heroTags: ["Cotton", "Pockets", "Three-quarter sleeves", "Not see-through"],
    heroProof: "Pocket visible in the photo",
    google: "Continue with Google",
    googleBusy: "Opening Google…",
    demo: "Continue in demo mode",
    demoBadge: "Testing only",
    demoNote: "Skips sign-in so you can try the screens. Nothing is saved to an account.",
    privacy: "From Google we keep only your name and email.",
    misconfiguredTitle: "Sign-in isn’t set up",
    misconfiguredBody:
      "This build has no Firebase settings. Add the NEXT_PUBLIC_FIREBASE_ values listed in .env.example, then build again.",
    errors: {
      network: "Couldn’t reach Google. Check your connection and try again.",
      unauthorizedDomain:
        "Sign-in isn’t allowed from this web address yet. Add it to the allowed domains in Firebase Authentication.",
      failed: "Sign-in didn’t finish. Try again.",
    },
  },

  account: {
    open: "Account",
    signedInAs: "Signed in as",
    demoUser: "You’re in demo mode",
    signOut: "Sign out",
  },

  home: {
    title: "What are you looking for?",
    hint: "Say what it must have, your budget and when you need it.",
    placeholder: "Cotton kurta, pockets, three-quarter sleeves, under ₹1,200, by Thursday",
    addPhoto: "Add photo",
    changePhoto: "Change photo",
    removePhoto: "Remove photo",
    photoAlt: "The photo you added",
    photoAdded: "Photo added",
    submit: "Make my checklist",
    submitting: "Reading your request…",
    examplesLabel: "Or tap an example",
    examples: [
      {
        label: "Cotton kurta with pockets under ₹1,200",
        text: "Cotton kurta with pockets under ₹1,200",
        needsPhoto: false,
      },
      {
        label: "Like this, but in white",
        text: "Like this, but in white",
        needsPhoto: true,
      },
      {
        label: "I saw someone wearing…",
        text: "I saw someone wearing a small watch with a green dial and a steel strap",
        needsPhoto: false,
      },
    ],
    needPhoto: "Add a photo to go with “Like this, but in white”.",
    emptyRequest: "Type what you’re looking for, or add a photo.",
    photoUnreadable: "That photo couldn’t be opened. Try a JPG or PNG.",
  },

  pincode: {
    title: "Where should it arrive?",
    body: "Your pincode lets us work out real delivery dates.",
    label: "Pincode",
    save: "Save pincode",
    invalid: "Enter a 6-digit pincode, like 411038.",
    required: "Add your pincode first.",
    deliveringTo: (pincode: string) => `Delivering to ${pincode}`,
    change: "Change",
  },

  understood: {
    back: "Change request",
    title: "Here’s what I understood",
    youAsked: "You asked for",
    quoted: (request: string) => `“${request}”`,
    withPhoto: "Matching your photo",
    question: "One question",
    answerLabel: "Your answer",
    answer: "Answer",
    skip: "Skip",
    mustHaves: "Must have",
    mustHavesHint: "We only show products that have every one of these. Tap one to change it.",
    noMustHaves: "Nothing required yet. Add a must-have, or we’ll show anything in this category.",
    rules: "Your rules",
    rulesHint: "From your saved rules. Switch one off for this search only.",
    ruleOff: "Off for this search",
    ruleSwitch: (label: string) => `Use “${label}” in this search`,
    niceToHaves: "Nice to have",
    niceToHavesHint: "These change the order, not what’s shown.",
    limits: "Budget and delivery",
    limitsHint: "Tap one to change it.",
    addMustHave: "Add a must-have",
    addMustHaveLabel: "What else must it have?",
    addMustHavePlaceholder: "Full sleeves",
    add: "Add",
    edit: (label: string) => `Edit “${label}”`,
    remove: (label: string) => `Remove “${label}”`,
    ruleChoices: {
      is: "Must be",
      is_not: "Must not be",
      present: "Must have",
      absent: "Must not have",
      at_most: "At most",
      at_least: "At least",
    },
    valueLabel: (name: string) => name,
    budgetLabel: "Budget, including delivery (₹)",
    budgetInvalid: "Enter an amount in rupees, like 1200.",
    deadlineLabel: "Arrives by",
    pincodeLabel: "Pincode",
    sizeLabel: "Size",
    saving: "Saving your change…",
    saved: "Checklist updated.",
    findMatches: "Find matches",
    noSessionTitle: "No checklist yet",
    noSessionBody: "Tell us what you’re looking for and we’ll make one.",
    startSearch: "Start a search",
  },

  results: {
    back: "Edit checklist",
    title: "Checking products",
    doneTitle: "Done checking",
    starting: "Looking for products that could match…",
    progress: (found: number, checking: number, passed: number) =>
      `Found ${found} · Checking ${checking} · ${passed} pass so far`,
    summary: (checked: number, passed: number) =>
      passed === 1
        ? `We checked ${checked}. 1 has everything you asked for.`
        : `We checked ${checked}. ${passed} have everything you asked for.`,
    unclear: (count: number) =>
      count === 1 ? "1 more we couldn’t fully confirm" : `${count} more we couldn’t fully confirm`,
    rejectedTitle: "Why others were rejected",
    tradeoffsTitle: "What to give up",
    tradeoffDeadline: (date: string) => `Allow ${date}`,
    tradeoffBudget: (amount: string) => `Allow ${amount}`,
    tradeoffMustHave: (name: string) => `Drop ${name.toLowerCase()}`,
    tradeoffMatches: (count: number) => (count === 1 ? "1 option" : `${count} options`),
    slotsLabel: "Products being checked",
    slot: {
      checking: "Checking",
      pass: "Has everything",
      fail: "Missing something",
      unclear: "Not sure",
    },
    slotLabel: (index: number, state: string) => `Product ${index}: ${state}`,
    liveFound: (count: number) => `Found ${count} products. Checking them now.`,
    stoppedEarly: "The check stopped before it finished. Check your connection and try again.",
    newSearch: "New search",
  },

  status: {
    pass: "Has it",
    fail: "Doesn’t have it",
    unclear: "Not sure",
  },

  errors: {
    network: "Couldn’t reach EcommAI. Check your connection and try again.",
    unauthorized: "Your sign-in has expired. Sign in again to continue.",
    busy: "EcommAI is busy right now. Try again in a moment.",
    badRequest: "That request didn’t work. Change it and try again.",
    notFound: "We couldn’t find that. Start a new search.",
    server: "Something went wrong on our side. Try again.",
  },

  labels: {
    is: (value: string, noun?: string) => (noun ? `${value} ${noun}` : value),
    isNot: (value: string, noun?: string) => (noun ? `no ${value} ${noun}` : `not ${value}`),
    present: (words: string) => words,
    absent: (words: string) => `no ${words}`,
    atMost: (name: string, value: string) => `${name} up to ${value}`,
    atLeast: (name: string, value: string) => `${name} at least ${value}`,
    unknownIs: (name: string, value: string) => `${name}: ${value}`,
    unknownIsNot: (name: string, value: string) => `${name}: not ${value}`,
    category: (category: string, gender: string | null) => (gender ? `${category} for ${gender}` : category),
    budget: (amount: string) => `Up to ${amount} incl. delivery`,
    addBudget: "Add a budget",
    deadline: (date: string) => `Arrives by ${date}`,
    addDeadline: "Add a date",
    pincode: (pincode: string) => `Deliver to ${pincode}`,
    addPincode: "Add your pincode",
    size: (size: string) => `Size ${size}`,
    addSize: "Add your size",
    genders: {
      men: "men",
      women: "women",
      boys: "boys",
      girls: "girls",
      unisex: "everyone",
    },
    categories: {
      Tshirts: "T-shirts",
      "Casual Shoes": "Casual shoes",
      "Sports Shoes": "Sports shoes",
      "Formal Shoes": "Formal shoes",
    } as Record<string, string>,
    values: {
      "three-quarter": "three-quarter",
      "v-neck": "V-neck",
      "a-line": "A-line",
      "see-through": "see-through",
    } as Record<string, string>,
    attributes: {
      sleeve_length: { name: "Sleeves", noun: "sleeves" },
      neckline: { name: "Neckline", noun: "neckline" },
      pockets: { name: "Pockets", noun: "pockets", presence: "pockets" },
      pattern: { name: "Pattern" },
      print_intensity: { name: "Print", noun: "print", presence: "print" },
      fabric: { name: "Fabric" },
      sheerness: { name: "Sheerness" },
      fit: { name: "Fit", noun: "fit" },
      length: { name: "Length", noun: "length" },
      closure: { name: "Closure", noun: "closure" },
      usage: {
        name: "Occasion",
        phrase: (value: string) => `for ${value}`,
        negated: (value: string) => `not for ${value}`,
      },
      rise: { name: "Rise", noun: "rise" },
      wash: { name: "Wash", noun: "wash" },
      distressed: { name: "Distressing", noun: "distressing", presence: "distressing" },
      stretch: { name: "Stretch", presence: "stretch" },
      type: { name: "Type" },
      sole_type: { name: "Sole", noun: "sole" },
      sole_colour: { name: "Sole colour", noun: "sole" },
      upper_material: { name: "Upper", noun: "upper" },
      logo_visible: { name: "Logo", noun: "logo", presence: "logo" },
      heel_height: { name: "Heel", noun: "heel" },
      display: { name: "Display", noun: "display" },
      dial_colour: { name: "Dial colour", noun: "dial" },
      dial_markings: {
        name: "Dial markings",
        presence: "dial markings",
        phrase: (value: string) => `${value} on the dial`,
        negated: (value: string) => `no ${value} on the dial`,
      },
      case_shape: { name: "Case shape", noun: "case" },
      case_material: { name: "Case", noun: "case" },
      case_size: { name: "Case size", noun: "case" },
      strap_material: { name: "Strap", noun: "strap" },
      strap_colour: { name: "Strap colour", noun: "strap" },
      chronograph: { name: "Chronograph", presence: "chronograph" },
      size: { name: "Size", noun: "size" },
      material: { name: "Material" },
      compartments: {
        name: "Compartments",
        phrase: (value: string) => `compartments: ${value}`,
        negated: (value: string) => `compartments: not ${value}`,
      },
      strap: { name: "Strap" },
      frame_shape: { name: "Frame", noun: "frame" },
      frame_material: { name: "Frame material", noun: "frame" },
      lens_colour: { name: "Lens", noun: "lenses" },
      polarised: { name: "Polarised", presence: "polarised lenses" },
      base_colour: { name: "Colour" },
      cod: { name: "Cash on delivery", presence: "cash on delivery" },
      other: { name: "Other" },
    } as Record<string, AttributeWords>,
  },
};

export type Strings = typeof en;
export type { AttributeWords };

export const strings: Strings = en;
