"use client";

import Image from "next/image";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { AppHeader } from "@/components/AppHeader";
import { CheckIcon, StoreIcon } from "@/components/icons";
import { ScreenPlaceholder } from "@/components/NoSession";
import { CompleteTheLook } from "@/components/product/CompleteTheLook";
import { getProduct, placeOrder } from "@/lib/api";
import { canRetry, errorMessage } from "@/lib/errors";
import { rupees, shortDay } from "@/lib/format";
import { clearSearch, findCard, resultsStore, sessionStore } from "@/lib/session";
import { useIsClient } from "@/lib/stored";
import { strings } from "@/lib/strings";
import type { ProductDetails, ShoppingBrief } from "@/lib/types";

const text = strings.checkout;
const ONE_SIZE = "One size";

type Load = { status: "loading" } | { status: "ready"; product: ProductDetails } | { status: "error"; message: string; retryable: boolean };
type Payment = "online" | "cod";

export function CheckoutScreen() {
  const params = useSearchParams();
  const isClient = useIsClient();
  const session = sessionStore.use();
  const [attempt, setAttempt] = useState(0);
  const productId = params.get("product");
  const size = params.get("size");

  return (
    <>
      <AppHeader back={productId ? { href: `/product/${encodeURIComponent(productId)}`, label: text.back } : undefined} />
      <main className="shell flex flex-col gap-6 pb-12 pt-2">
        <section className="rounded-[1.25rem] border-2 border-dashed border-marigold bg-marigold-wash p-5">
          <h1 className="display flex items-center gap-2.5 text-[2rem]">
            <StoreIcon className="size-8 flex-none" />
            {text.badge}
          </h1>
          <p className="mt-2">{text.banner}</p>
        </section>
        {!isClient ? (
          <ScreenPlaceholder />
        ) : !productId ? (
          <p className="panel p-5">{text.missing}</p>
        ) : (
          <CheckoutLoader
            key={`${productId}:${attempt}`}
            productId={productId}
            size={size}
            sessionId={session?.sessionId}
            brief={session?.brief ?? null}
            onRetry={() => setAttempt((n) => n + 1)}
          />
        )}
      </main>
    </>
  );
}

interface CheckoutLoaderProps {
  productId: string;
  size: string | null;
  sessionId: string | undefined;
  brief: ShoppingBrief | null;
  onRetry: () => void;
}

function CheckoutLoader({ productId, size, sessionId, brief, onRetry }: CheckoutLoaderProps) {
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
        {load.retryable ? (
          <button type="button" className="btn btn-primary btn-small mt-4" onClick={onRetry}>
            {strings.common.tryAgain}
          </button>
        ) : null}
      </div>
    );
  }
  return <Order product={load.product} size={size} brief={brief} />;
}

function wantsCod(brief: ShoppingBrief | null): boolean {
  return Boolean(brief?.must_haves.some((m) => m.attribute === "cod" && (m.rule === "present" || m.rule === "is")));
}

function Order({ product, size, brief }: { product: ProductDetails; size: string | null; brief: ShoppingBrief | null }) {
  const codAvailable = product.cod?.available ?? false;
  const codFee = product.cod?.fee ?? 0;
  const [payment, setPayment] = useState<Payment>(codAvailable && wantsCod(brief) ? "cod" : "online");
  const [placing, setPlacing] = useState(false);
  const [placed, setPlaced] = useState(false);
  const [placeError, setPlaceError] = useState<string | null>(null);
  const placedHeading = useRef<HTMLHeadingElement>(null);
  const titleId = useId();
  const paymentName = useId();
  const hasResults = resultsStore.use() !== null;

  const item = product.price?.selling ?? 0;
  const fee = payment === "cod" ? codFee : 0;
  const total = item + product.delivery_fee + fee;
  const deliveryDate = product.delivery_date ?? findCard(product.id)?.delivery_date ?? null;
  const thumb = product.images?.thumb ?? null;

  useEffect(() => {
    if (placed) placedHeading.current?.focus();
  }, [placed]);

  async function place() {
    setPlacing(true);
    setPlaceError(null);
    try {
      await placeOrder(product.id, size);
      setPlaced(true);
    } catch (caught) {
      setPlaceError(errorMessage(caught));
    } finally {
      setPlacing(false);
    }
  }

  if (placed) {
    return (
      <section className="panel p-6" aria-labelledby={titleId}>
        <span className="flex size-12 items-center justify-center rounded-full bg-pass text-white">
          <CheckIcon className="size-7" strokeWidth={3} />
        </span>
        <h2 id={titleId} ref={placedHeading} tabIndex={-1} className="display mt-4 text-[1.75rem] focus:outline-none">
          {text.placedTitle}
        </h2>
        <p className="mt-2">{text.placedBody}</p>
        <div className="mt-5 flex flex-col gap-3">
          {hasResults ? (
            <Link href="/results" className="btn btn-primary w-full">
              {text.backToResults}
            </Link>
          ) : null}
          <Link href="/" className={`btn w-full ${hasResults ? "btn-secondary" : "btn-primary"}`} onClick={() => clearSearch()}>
            {strings.results.newSearch}
          </Link>
        </div>
        {product.source === "catalog" ? (
          <div className="mt-6 flex flex-col gap-3">
            <hr className="stitch" />
            <CompleteTheLook product={product} gender={null} />
          </div>
        ) : null}
      </section>
    );
  }

  return (
    <section aria-labelledby={titleId} className="flex flex-col gap-5">
      <h2 id={titleId} className="eyebrow">
        {text.title}
      </h2>
      <div className="panel flex items-center gap-4 p-4">
        {thumb ? (
          <span className="block w-16 flex-none overflow-hidden rounded-lg ring-1 ring-line">
            <Image src={thumb} alt="" width={300} height={400} unoptimized className="block h-auto w-full" />
          </span>
        ) : null}
        <div className="min-w-0">
          {product.brand ? <p className="eyebrow text-[0.8125rem]">{product.brand}</p> : null}
          <p className="font-semibold leading-snug">{product.title}</p>
          {size ? <p className="text-ink-soft">{size === ONE_SIZE ? strings.product.oneSize : text.size(size)}</p> : null}
        </div>
      </div>

      {codAvailable ? (
        <fieldset>
          <legend className="mb-2 font-semibold">{text.payment}</legend>
          <div className="choices">
            {(["online", "cod"] as const).map((choice) => (
              <span key={choice} className="contents">
                <input
                  type="radio"
                  id={`${paymentName}-${choice}`}
                  name={paymentName}
                  value={choice}
                  checked={payment === choice}
                  onChange={() => setPayment(choice)}
                />
                <label htmlFor={`${paymentName}-${choice}`}>
                  {payment === choice ? <CheckIcon className="size-4" strokeWidth={3} /> : null}
                  {choice === "online" ? text.payOnline : text.payCod(rupees(codFee))}
                </label>
              </span>
            ))}
          </div>
        </fieldset>
      ) : null}

      <dl className="divide-y divide-line rounded-2xl bg-card px-4 ring-1 ring-line">
        <Row label={text.item} value={rupees(item)} />
        <Row label={text.delivery} value={product.delivery_fee > 0 ? rupees(product.delivery_fee) : text.free} />
        {fee > 0 ? <Row label={text.codFee} value={rupees(fee)} /> : null}
        <Row label={text.total} value={rupees(total)} strong />
      </dl>
      {deliveryDate ? <p className="font-semibold">{text.arrivesBy(shortDay(deliveryDate))}</p> : null}
      <p>
        <span className="demo-badge">{strings.card.demo(product.price?.demo ?? true)}</span>
      </p>

      {placeError ? (
        <p role="alert" className="field-error">
          {placeError}
        </p>
      ) : null}
      <button type="button" className="btn btn-primary w-full" onClick={() => void place()} disabled={placing} aria-busy={placing}>
        {placing ? text.placing : text.place}
      </button>
    </section>
  );
}

function Row({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-3">
      <dt className={strong ? "font-semibold" : "text-ink-soft"}>{label}</dt>
      <dd className={`tabular-nums ${strong ? "display text-[1.5rem]" : "font-semibold"}`}>{value}</dd>
    </div>
  );
}
