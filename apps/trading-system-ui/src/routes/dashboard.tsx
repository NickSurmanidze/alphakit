import { trpc } from '@/lib/trpc';

export const DashboardPage = () => {
  const me = trpc.auth.me.useQuery();
  const instruments = trpc.instruments.list.useQuery();

  return (
    <div className="flex flex-col gap-2">
      <h1 className="text-xl font-semibold">Dashboard</h1>
      {me.isLoading ? <p>Loading...</p> : null}
      {me.data ? <p className="text-muted-foreground">Signed in as {me.data.email}</p> : null}
      <p className="text-muted-foreground">
        {instruments.data ? instruments.data.length : '…'} instrument(s) tracked.
      </p>
    </div>
  );
};
