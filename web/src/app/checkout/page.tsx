import type { Metadata } from "next";
import { Suspense } from "react";
import { CheckoutScreen } from "@/components/checkout/CheckoutScreen";
import { AppHeader } from "@/components/AppHeader";
import { ScreenPlaceholder } from "@/components/NoSession";
import { strings } from "@/lib/strings";

export const metadata: Metadata = {
  title: strings.meta.pageTitle(strings.checkout.pageTitle),
};

function Loading() {
  return (
    <>
      <AppHeader />
      <main className="shell pt-2">
        <ScreenPlaceholder />
      </main>
    </>
  );
}

export default function CheckoutPage() {
  return (
    <Suspense fallback={<Loading />}>
      <CheckoutScreen />
    </Suspense>
  );
}
