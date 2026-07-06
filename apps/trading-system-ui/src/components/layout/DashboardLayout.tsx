import type { ReactNode } from 'react';
import { NavLink, useLocation, useParams } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';
import { trpc } from '@/lib/trpc';

const navLinkClasses = ({ isActive }: { isActive: boolean }) =>
  cn(
    'block rounded-md px-2 py-1.5 text-sm font-medium transition-colors hover:bg-muted hover:text-foreground',
    isActive ? 'bg-muted text-foreground' : 'text-muted-foreground'
  );

const childNavLinkClasses = ({ isActive }: { isActive: boolean }) =>
  cn(
    'block rounded-md px-2 py-1 text-sm transition-colors hover:bg-muted hover:text-foreground',
    isActive ? 'bg-muted text-foreground' : 'text-muted-foreground'
  );

// The Instruments group's second-level nav: "List" always, plus the instrument currently being
// viewed (if any) so the sidebar reflects where you actually are within that section, not just
// that you're somewhere under it.
const InstrumentsNavGroup = () => {
  const location = useLocation();
  const { id } = useParams<{ id: string }>();
  const isUnderInstruments = location.pathname.startsWith('/instruments');
  const viewingInstrumentId = location.pathname.startsWith('/instruments/') ? id : undefined;

  // Cheap: trpc/react-query dedupes this against the same query already used by the list and
  // detail pages, so this doesn't add a real extra request when either of those is also mounted.
  const instruments = trpc.instruments.list.useQuery(undefined, { enabled: Boolean(viewingInstrumentId) });
  const viewingInstrument = instruments.data?.find(i => i.id === viewingInstrumentId);

  return (
    <div className="flex flex-col gap-0.5">
      <div className={cn('px-2 py-1.5 text-sm font-medium', isUnderInstruments ? 'text-foreground' : 'text-muted-foreground')}>
        Instruments
      </div>
      <div className="ml-2 flex flex-col gap-0.5 border-l border-border pl-2">
        <NavLink to="/instruments" end className={childNavLinkClasses}>
          List
        </NavLink>
        {viewingInstrument ? (
          <NavLink to={`/instruments/${viewingInstrument.id}`} className={childNavLinkClasses}>
            {viewingInstrument.displaySymbol}
          </NavLink>
        ) : null}
      </div>
    </div>
  );
};

export const DashboardLayout = ({ children }: { children: ReactNode }) => {
  const { logout } = useAuth();

  return (
    <div className="flex h-screen overflow-hidden">
      <aside className="flex w-56 shrink-0 flex-col border-r border-border bg-muted/30">
        {/* Sticky header -- stays put while the nav list below scrolls. */}
        <div className="shrink-0 border-b border-border p-4 text-lg font-semibold">Trading System</div>

        <nav className="flex-1 overflow-y-auto p-4">
          <div className="flex flex-col gap-1">
            <NavLink to="/" end className={navLinkClasses}>
              Dashboard
            </NavLink>

            <InstrumentsNavGroup />

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

      <main className="flex-1 overflow-y-auto p-6">{children}</main>
    </div>
  );
};
