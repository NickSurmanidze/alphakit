import type { ReactNode } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';

import { DashboardLayout } from '@/components/layout/DashboardLayout';
import { useAuth } from '@/lib/auth';
import { DashboardPage } from '@/routes/dashboard';
import { InstrumentDetailPage } from '@/routes/instrumentDetail';
import { InstrumentsPage } from '@/routes/instruments';
import { LoginPage } from '@/routes/login';

const ProtectedRoute = ({ children }: { children: ReactNode }) => {
  const { user, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) {
    return <div className="flex min-h-screen items-center justify-center">Loading...</div>;
  }

  if (!user) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }

  return <DashboardLayout>{children}</DashboardLayout>;
};

export const App = () => {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/"
        element={
          <ProtectedRoute>
            <DashboardPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/instruments"
        element={
          <ProtectedRoute>
            <InstrumentsPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/instruments/:id"
        element={
          <ProtectedRoute>
            <InstrumentDetailPage />
          </ProtectedRoute>
        }
      />
    </Routes>
  );
};
