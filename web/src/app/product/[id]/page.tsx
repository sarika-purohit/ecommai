import type { Metadata } from "next";
import { Suspense } from "react";
import { AppHeader } from "@/components/AppHeader";
import { ScreenPlaceholder } from "@/components/NoSession";
import { ProductScreen } from "@/components/product/ProductScreen";
import { strings } from "@/lib/strings";

export const metadata: Metadata = {
  title: strings.meta.pageTitle(strings.product.pageTitle),
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

export default function ProductPage({ params }: { params: Promise<{ id: string }> }) {
  return (
    <Suspense fallback={<Loading />}>
      {params.then(({ id }) => (
        <ProductScreen productId={decodeURIComponent(id)} />
      ))}
    </Suspense>
  );
}
