"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState, type ReactNode, type RefObject } from "react";
import { AppHeader } from "@/components/AppHeader";
import { CheckIcon, ChevronDownIcon, ExternalIcon, ShieldIcon } from "@/components/icons";
import { ScreenPlaceholder } from "@/components/NoSession";
import { TickList } from "@/components/proof/TickList";
import { checkout, getProduct, sendFeedback } from "@/lib/api";
import { canRetry, errorMessage } from "@/lib/errors";
import { rupees, shortDay } from "@/lib/format";
import { totalNote } from "@/lib/price";
import { findCard, pincodeStore, resultsStore, sessionStore } from "@/lib/session";
import { useIsClient } from "@/lib/stored";
import { strings } from "@/lib/strings";
import type { ProductDetails, Review, ShoppingBrief } from "@/lib/types";

const text = strings.product;
const ONE_SIZE = "One size";

type Load = { status: "loading" } | { status: "ready"; product: ProductDetails } | { status: "error"; message: string; retryable: boolean };

export function ProductScreen({ productId }: { productId: string }) {
  const isClient = useIsClient();
  const session = sessionStore.use();
  const results = resultsStore.use();
  const [attempt, setAttempt] = useState(0);
  const fromResults = Boolean(session && results && results.sessionId === session.sessionId);

  return (
    <>
      <AppHeader back={fromResults ? { href: "/results", label: text.back } : { href: "/", label: text.backHome }} />
      <main className="shell pb-40 pt-2">
        {isClient ? (
          <ProductLoader
            key={`${productId}:${attempt}`}
            productId={productId}
            sessionId={session?.sessionId}
            briefs={{ session: session?.brief ?? null, results: fromResults && results ? results.brief : null }}
            onRetry={() => setAttempt((n) => n + 1)}
          />
        ) : (
          <ScreenPlaceholder />
        )}
      </main>
    </>
  );
}

/** The agent's ticks follow the session's current checklist; a result card's ticks follow the one it was searched with. */
interface Briefs {
  session: ShoppingBrief | null;
  results: ShoppingBrief | null;
}

interface ProductLoaderProps {
  productId: string;
  sessionId: string | undefined;
  briefs: Briefs;
  onRetry: () => void;
}

function ProductLoader({ productId, sessionId, briefs, onRetry }: ProductLoaderProps) {
  const [load, setLoad] = useState<Load>({ status: "loading" });

  useEffect(() => {
    let live = true;
    getProduct(productId, sessionId).then(
      (product) => live && setLoad({ status: "ready", product }),
      (error: unknown) => live && setLoad({ status: "error", message: errorMessage(error), retryable: canRetry(error) }),
    );
    return () => {
      live = false;
    };
  }, [productId, sessionId]);

  if (load.status === "loading") return <ScreenPlaceholder />;
  if (load.status === "error") {
    return (
      <div role="alert" className="panel p-5">
        <p className="font-semibold text-fail">{load.message}</p>
        <div className="mt-4 flex flex-wrap gap-2">
          {load.retryable ? (
            <button type="button" className="btn btn-primary btn-small" onClick={onRetry}>
              {strings.common.tryAgain}
            </button>
          ) : null}
          <Link href="/" className="btn btn-secondary btn-small">
            {strings.understood.startSearch}
          </Link>
        </div>
      </div>
    );
  }
  return <Product product={load.product} sessionId={sessionId} briefs={briefs} />;
}

function sizesInStock(product: ProductDetails): string[] {
  if (product.sizes_in_stock) return product.sizes_in_stock;
  return Object.entries(product.sizes)
    .filter(([, count]) => count > 0)
    .map(([size]) => size);
}

function Product({ product, sessionId, briefs }: { product: ProductDetails; sessionId: string | undefined; briefs: Briefs }) {
  const router = useRouter();
  const card = findCard(product.id);
  const brief = briefs.session;
  const pincode = brief?.pincode ?? pincodeStore.get();
  const inStock = sizesInStock(product);
  const outOfStock = Object.keys(product.sizes).filter((size) => !inStock.includes(size));
  const oneSize = inStock.length === 1 && inStock[0] === ONE_SIZE;
  const wanted = brief?.size ? inStock.find((size) => size.toLowerCase() === brief.size?.toLowerCase()) : undefined;
  const [size, setSize] = useState<string | null>(oneSize ? ONE_SIZE : (wanted ?? null));
  const [sizeError, setSizeError] = useState(false);
  const [buying, setBuying] = useState(false);
  const [buyError, setBuyError] = useState<string | null>(null);
  const sizeGroup = useRef<HTMLFieldSetElement>(null);

  const selling = product.price?.selling ?? null;
  const total = product.total_price ?? card?.total_price ?? (selling !== null ? selling + product.delivery_fee : null);
  const deliveryDate = product.delivery_date ?? card?.delivery_date ?? null;
  const priced = total !== null ? { total_price: total, price: product.price, delivery_fee: product.delivery_fee, cod: product.cod } : null;
  const [ticks, tickBrief] = product.ticks ? [product.ticks, briefs.session] : [card?.ticks ?? [], briefs.results];
  const photo = product.images?.gemini ?? card?.photo ?? null;
  const original = product.images?.original ?? card?.original ?? null;
  const mrp = product.price && product.price.mrp > product.price.selling ? product.price.mrp : null;

  async function buy() {
    if (!size) {
      setSizeError(true);
      sizeGroup.current?.querySelector("input")?.focus();
      return;
    }
    setBuying(true);
    setBuyError(null);
    try {
      const { redirect_url } = await checkout(product.id, size);
      if (sessionId) void sendFeedback(sessionId, product.id, "buy").catch(() => undefined);
      const url = new URL(redirect_url, window.location.origin);
      if (url.origin === window.location.origin) router.push(`${url.pathname}${url.search}`);
      else window.location.assign(url.href);
    } catch (caught) {
      setBuyError(errorMessage(caught));
      setBuying(false);
    }
  }

  return (
    <article className="flex flex-col gap-6">
      {photo ? (
        <div>
          <div className="overflow-hidden rounded-[1.25rem] bg-card ring-1 ring-line">
            <Image
              src={photo}
              alt={product.title}
              width={750}
              height={1000}
              unoptimized
              loading="eager"
              fetchPriority="high"
              className="block h-auto w-full"
            />
          </div>
          {original ? (
            <a href={original} target="_blank" rel="noopener noreferrer" className="link mt-1">
              {text.fullPhoto}
              <ExternalIcon className="size-4" />
            </a>
          ) : null}
        </div>
      ) : null}

      <div>
        {product.brand ? <p className="eyebrow">{product.brand}</p> : null}
        <h1 className="display mt-1 text-[2rem]">{product.title}</h1>
        {total !== null && priced ? (
          <div className="mt-4">
            <p className="flex flex-wrap items-baseline gap-x-3">
              <span className="display text-[2.5rem] tabular-nums">{rupees(total)}</span>
              {mrp ? (
                <span className="text-ink-soft">
                  <s>{text.mrp(rupees(mrp))}</s>{" "}
                  <span className="font-semibold text-pass">{text.off(Math.round((1 - (product.price?.selling ?? mrp) / mrp) * 100))}</span>
                </span>
              ) : null}
            </p>
            <p className="text-ink-soft">{totalNote(priced)}</p>
          </div>
        ) : null}

        <dl className="mt-4 divide-y divide-line rounded-2xl bg-card px-4 ring-1 ring-line">
          <Fact label={text.factDelivery}>
            {deliveryDate ? text.arrivesBy(shortDay(deliveryDate)) : strings.card.noDate}
            {deliveryDate && pincode ? ` ${text.deliverTo(pincode)}` : null}
          </Fact>
          {product.returns ? (
            <Fact label={text.factReturns}>
              {product.returns.returnable ? text.returns(product.returns.window_days, product.returns.exchange) : text.noReturns}
            </Fact>
          ) : null}
          {product.cod ? (
            <Fact label={text.factPayment}>{product.cod.available ? text.cod(rupees(product.cod.fee)) : strings.card.noCod}</Fact>
          ) : null}
        </dl>
        {product.demo_data ? (
          <p className="mt-3">
            <span className="demo-badge">{strings.card.demo(product.price?.demo ?? true)}</span>
          </p>
        ) : null}
      </div>

      {ticks.length > 0 ? (
        <Section title={text.checklist} hint={strings.results.proofHint}>
          <TickList ticks={ticks} brief={tickBrief} photo={photo} />
        </Section>
      ) : null}

      <Section title={text.size}>
        {inStock.length === 0 ? (
          <p className="text-ink-soft">{text.soldOut}</p>
        ) : oneSize ? (
          <p className="font-semibold">{text.oneSize}</p>
        ) : (
          <SizePicker
            groupRef={sizeGroup}
            sizes={inStock}
            value={size}
            invalid={sizeError}
            onChange={(next) => {
              setSize(next);
              setSizeError(false);
            }}
          />
        )}
        {outOfStock.length > 0 && !oneSize ? <p className="text-base text-ink-soft">{text.outOfStock(outOfStock)}</p> : null}
      </Section>

      <Reviews product={product} />

      {product.description ? (
        <Section title={text.about}>
          <p className="leading-relaxed">{product.description}</p>
        </Section>
      ) : null}

      <div className="fixed inset-x-0 bottom-0 z-10 bg-gradient-to-t from-paper from-70% to-transparent pb-[max(1rem,env(safe-area-inset-bottom))] pt-6">
        <div className="shell">
          {buyError ? (
            <p role="alert" className="field-error mb-2">
              {buyError}
            </p>
          ) : null}
          {sizeError ? (
            <p role="alert" className="field-error mb-2">
              {text.pickSize}
            </p>
          ) : null}
          <button
            type="button"
            className="btn btn-primary w-full"
            onClick={() => void buy()}
            disabled={buying || inStock.length === 0}
            aria-busy={buying}
          >
            {buying ? text.buying : text.buy}
            {buying || total === null ? null : <span className="font-body text-[1.0625rem] font-medium opacity-85">· {rupees(total)}</span>}
          </button>
        </div>
      </div>
    </article>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-3">
      <dt className="flex-none text-base text-ink-soft">{label}</dt>
      <dd className="text-right font-semibold leading-snug">{children}</dd>
    </div>
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <hr className="stitch" />
      <div>
        <h2 id={id} className="eyebrow pt-2">
          {title}
        </h2>
        {hint ? <p className="mt-1 text-base text-ink-soft">{hint}</p> : null}
      </div>
      {children}
    </section>
  );
}

interface SizePickerProps {
  groupRef: RefObject<HTMLFieldSetElement | null>;
  sizes: string[];
  value: string | null;
  invalid: boolean;
  onChange: (size: string) => void;
}

function SizePicker({ groupRef, sizes, value, invalid, onChange }: SizePickerProps) {
  const name = useId();
  return (
    <fieldset ref={groupRef} aria-invalid={invalid || undefined}>
      <legend className="sr-only">{text.size}</legend>
      <div className="choices">
        {sizes.map((size) => (
          <span key={size} className="contents">
            <input
              type="radio"
              id={`${name}-${size}`}
              name={name}
              value={size}
              checked={value === size}
              onChange={() => onChange(size)}
            />
            <label htmlFor={`${name}-${size}`} className={invalid ? "border-fail" : undefined}>
              {value === size ? <CheckIcon className="size-4" strokeWidth={3} /> : null}
              {size}
            </label>
          </span>
        ))}
      </div>
    </fieldset>
  );
}

function Reviews({ product }: { product: ProductDetails }) {
  const [showHidden, setShowHidden] = useState(false);
  const hiddenId = useId();
  const shown = product.reviews.filter((review) => !review.flagged);
  const hidden = product.reviews.filter((review) => review.flagged);

  return (
    <Section title={text.reviews}>
      {product.rating_avg !== null && product.rating_count > 0 ? (
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="font-semibold">{text.reviewSummary(product.rating_avg.toFixed(1), product.rating_count)}</span>
          {product.reviews.some((review) => review.demo) ? <span className="demo-badge">{strings.proof.demoReviews}</span> : null}
        </p>
      ) : null}
      {shown.length === 0 ? <p className="text-ink-soft">{text.noReviews}</p> : null}
      <ul className="flex flex-col gap-2">
        {shown.map((review, index) => (
          <ReviewItem key={index} review={review} />
        ))}
      </ul>
      {hidden.length > 0 ? (
        <div>
          <button
            type="button"
            className="flex min-h-12 items-center gap-2 font-semibold text-unclear"
            aria-expanded={showHidden}
            aria-controls={hiddenId}
            onClick={() => setShowHidden((open) => !open)}
          >
            <ShieldIcon className="size-5" />
            {text.hiddenReviews(hidden.length)}
            <ChevronDownIcon className={`size-4 transition-transform ${showHidden ? "rotate-180" : ""}`} />
          </button>
          <div id={hiddenId} hidden={!showHidden}>
            <p className="text-base text-ink-soft">{text.hiddenHint}</p>
            <ul className="mt-2 flex flex-col gap-2">
              {hidden.map((review, index) => (
                <ReviewItem key={index} review={review} />
              ))}
            </ul>
          </div>
        </div>
      ) : null}
    </Section>
  );
}

function ReviewItem({ review }: { review: Review }) {
  return (
    <li className={`rounded-2xl p-4 ring-1 ring-line ${review.flagged ? "bg-unclear-wash" : "bg-card"}`}>
      <p className="text-base font-semibold text-ink-soft">
        <span aria-hidden="true">{"★".repeat(review.rating)}</span>
        <span className="sr-only">{text.stars(review.rating)}</span>
      </p>
      <p className="mt-1 leading-snug">{review.text}</p>
    </li>
  );
}
