import type { Metadata } from "next";
import { ProfileScreen } from "@/components/profile/ProfileScreen";
import { strings } from "@/lib/strings";

export const metadata: Metadata = {
  title: strings.meta.pageTitle(strings.profile.pageTitle),
};

export default function ProfilePage() {
  return <ProfileScreen />;
}
