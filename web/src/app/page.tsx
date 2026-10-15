import { Suspense } from "react";
import { StoreScreen } from "@/components/store/StoreScreen";

export default function StorePage() {
  // The store reads its search and filters from the address, which needs a Suspense boundary.
  return (
    <Suspense fallback={<div className="min-h-dvh" aria-busy="true" />}>
      <StoreScreen />
    </Suspense>
  );
}
