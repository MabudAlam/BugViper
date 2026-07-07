"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { Skeleton } from "@/components/ui/skeleton";

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) {
      router.replace("/login");
    }
  }, [user, loading, router]);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-background">
        <div className="space-y-4 w-full max-w-md px-4">
          <Skeleton className="h-8 w-48 rounded-base" />
          <Skeleton className="h-32 w-full rounded-base" />
          <Skeleton className="h-32 w-full rounded-base" />
        </div>
      </div>
    );
  }

  if (!user) return null;

  return <>{children}</>;
}
