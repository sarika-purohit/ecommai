// The detail vocabulary from data_prep/schemas.py (PLAN.md appendix H), for the personal-rule editor.
// Values are what the agent stores; labels.ts turns them into the shopper's words.

export type RuleKind = "presence" | "text";

export interface RuleDetail {
  attribute: string;
  kind: RuleKind;
  /** Allowed values for "is" and "is not" rules. */
  values?: string[];
}

const COLOURS = [
  "black", "white", "grey", "blue", "green", "red", "pink", "purple",
  "yellow", "orange", "brown", "beige", "gold", "silver", "multi",
];

/** Details worth a standing rule, most asked-for first. A rule only applies to products that have the detail. */
export const RULE_DETAILS: RuleDetail[] = [
  { attribute: "fabric", kind: "text", values: ["cotton", "linen", "rayon", "polyester", "silk", "blend", "denim"] },
  { attribute: "cod", kind: "presence" },
  { attribute: "returnable", kind: "presence" },
  { attribute: "exchange", kind: "presence" },
  { attribute: "colour", kind: "text", values: COLOURS },
  { attribute: "pockets", kind: "presence" },
  { attribute: "sheerness", kind: "text", values: ["opaque", "slightly sheer", "see-through"] },
  { attribute: "print_intensity", kind: "text", values: ["none", "subtle", "medium", "loud"] },
  { attribute: "sleeve_length", kind: "text", values: ["sleeveless", "short", "elbow", "three-quarter", "full"] },
  {
    attribute: "neckline",
    kind: "text",
    values: ["round", "v-neck", "collar", "mandarin", "boat", "square", "keyhole", "henley", "polo", "cowl"],
  },
  { attribute: "fit", kind: "text", values: ["slim", "regular", "relaxed", "oversized", "a-line", "straight", "skinny", "wide-leg"] },
  { attribute: "logo_visible", kind: "text", values: ["none", "subtle", "prominent"] },
  { attribute: "upper_material", kind: "text", values: ["canvas", "leather", "synthetic leather", "mesh", "knit", "suede"] },
  { attribute: "heel_height", kind: "text", values: ["flat", "low", "medium", "high"] },
  { attribute: "strap_material", kind: "text", values: ["steel", "leather", "silicone", "fabric", "mesh", "ceramic"] },
  { attribute: "material", kind: "text", values: ["synthetic leather", "leather", "canvas", "fabric", "jute"] },
  { attribute: "size_fit", kind: "text", values: ["true to size", "runs small", "runs large"] },
  { attribute: "colour_fastness", kind: "text", values: ["good", "fades"] },
];

export function ruleDetail(attribute: string): RuleDetail | undefined {
  return RULE_DETAILS.find((detail) => detail.attribute === attribute);
}
