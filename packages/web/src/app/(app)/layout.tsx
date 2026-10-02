import { Sidebar } from "@/components/ui/sidebar";
import { ToastProvider } from "@/components/ui/toast";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <ToastProvider>
      <Sidebar />
      <div className="pl-60">
        <main className="mx-auto max-w-5xl px-8 py-8">{children}</main>
      </div>
    </ToastProvider>
  );
}
