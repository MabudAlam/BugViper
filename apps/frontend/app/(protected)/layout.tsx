import { AuthGuard } from "@/components/auth-guard";
import { Header } from "@/components/header";
import { Sidebar } from "@/components/sidebar";

export default function ProtectedLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGuard>
      <Sidebar />
      <div className="min-h-screen flex flex-col bg-background md:ml-56">
        <Header />
        <main className="flex-1 p-4 md:p-8">{children}</main>
      </div>
    </AuthGuard>
  );
}
