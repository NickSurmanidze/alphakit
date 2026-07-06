import { Button } from '@/components/ui/button';
import { useAuth } from '@/lib/auth';
import { trpc } from '@/lib/trpc';

export const DashboardPage = () => {
  const { logout } = useAuth();
  const me = trpc.auth.me.useQuery();

  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 p-4">
      <h1 className="text-xl font-semibold">Trading System</h1>
      {me.isLoading ? <p>Loading...</p> : null}
      {me.data ? <p>Signed in as {me.data.email}</p> : null}
      <Button variant="outline" onClick={() => logout()}>
        Log out
      </Button>
    </div>
  );
};
