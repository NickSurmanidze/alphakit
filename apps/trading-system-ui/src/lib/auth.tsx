import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { httpBatchLink } from '@trpc/client';
import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';

import { trpc } from './trpc';

interface AuthUser {
  id: string;
  email: string;
}

// Mutable module-level token, read synchronously by the tRPC link's `headers()` callback
// on every request. Deliberately never persisted to localStorage/sessionStorage (XSS exposure) --
// it lives only in memory and is restored via the httpOnly refresh cookie on page load.
let currentAccessToken: string | null = null;

const queryClient = new QueryClient();

const trpcClient = trpc.createClient({
  links: [
    httpBatchLink({
      url: '/trpc',
      fetch: (url, options) => fetch(url, { ...options, credentials: 'include' }),
      headers: () => (currentAccessToken ? { authorization: `Bearer ${currentAccessToken}` } : {})
    })
  ]
});

interface AuthContextValue {
  user: AuthUser | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export const AuthProvider = ({ children }: { children: ReactNode }) => {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    trpcClient.auth.refresh
      .mutate()
      .then(result => {
        currentAccessToken = result.accessToken;
        setUser(result.user);
      })
      .catch(() => {
        currentAccessToken = null;
        setUser(null);
      })
      .finally(() => setIsLoading(false));
  }, []);

  const login = async (email: string, password: string) => {
    const result = await trpcClient.auth.login.mutate({ email, password });
    currentAccessToken = result.accessToken;
    setUser(result.user);
  };

  const logout = async () => {
    try {
      await trpcClient.auth.logout.mutate();
    } finally {
      currentAccessToken = null;
      setUser(null);
    }
  };

  return (
    <trpc.Provider client={trpcClient} queryClient={queryClient}>
      <QueryClientProvider client={queryClient}>
        <AuthContext.Provider value={{ user, isLoading, login, logout }}>
          {children}
        </AuthContext.Provider>
      </QueryClientProvider>
    </trpc.Provider>
  );
};

export const useAuth = (): AuthContextValue => {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return ctx;
};
