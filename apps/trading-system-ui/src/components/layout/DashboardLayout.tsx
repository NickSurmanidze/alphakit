import type { ReactNode } from 'react';
import { LayoutDashboard, LineChart } from 'lucide-react';
import { NavLink, useLocation } from 'react-router-dom';

import { ErrorBoundary } from '@/components/ErrorBoundary';
import { Button } from '@/components/ui/button';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';

const navLinkClasses = ({ isActive }: { isActive: boolean }) =>
  cn(
    'flex items-center gap-2 rounded-md px-2 py-1.5 text-sm font-medium transition-colors hover:bg-muted hover:text-foreground',
    isActive ? 'bg-muted text-foreground' : 'text-muted-foreground'
  );

export const DashboardLayout = ({ children }: { children: ReactNode }) => {
  const { logout } = useAuth();
  const location = useLocation();

  return (
    <div className="flex h-screen overflow-hidden">
      <aside className="flex w-56 shrink-0 flex-col border-r border-border bg-muted/30">
        {/* Sticky header -- stays put while the nav list below scrolls. */}
        <div className="shrink-0 border-b border-border p-4 text-lg font-semibold">Trading System</div>

        <nav className="flex-1 overflow-y-auto p-4">
          <div className="flex flex-col gap-1">
            <NavLink to="/" end className={navLinkClasses}>
              <LayoutDashboard className="size-4" />
              Dashboard
            </NavLink>

            <NavLink to="/instruments" className={navLinkClasses}>
              <LineChart className="size-4" />
              Instruments
            </NavLink>

            {/* Opens in a new tab; auth is via the httpOnly refresh cookie, sent automatically --
                no token to pass through a plain link. */}
            <a
              href="/admin/queues"
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-md px-2 py-1.5 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              Bull Board ↗
            </a>
          </div>
        </nav>

        {/* Sticky footer -- stays put while the nav list above scrolls. */}
        <div className="shrink-0 border-t border-border p-4">
          <Button variant="outline" size="sm" className="w-full" onClick={() => logout()}>
            Log out
          </Button>
        </div>
      </aside>

      <main className="flex-1 overflow-y-auto p-6">
        <ErrorBoundary key={location.pathname}>{children}</ErrorBoundary>
      </main>
    </div>
  );
};
