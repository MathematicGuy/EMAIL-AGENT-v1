import { useState, useEffect, lazy, Suspense } from 'react';
import { LandingPage } from './landing/LandingPage';
import { MemoryDevOverlay } from './lib/observability/MemoryDevOverlay';

// Code-split heavy routes (Dashboard & Documents) to minimize initial RAM footprint
const Dashboard = lazy(() => import('./dashboard/Dashboard'));
const DocumentsDemo = lazy(() =>
  import('./modules/documents/DocumentsDemo').then((m) => ({ default: m.DocumentsDemo }))
);

function RouteFallback() {
  return (
    <div className="flex h-screen w-screen items-center justify-center bg-zinc-950 text-zinc-400 font-mono text-sm">
      <div className="flex items-center gap-3">
        <div className="h-4 w-4 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent" />
        <span>Loading workspace module...</span>
      </div>
    </div>
  );
}

export function App() {
  const [route, setRoute] = useState<'landing' | 'dashboard' | 'documents'>(() => {
    if (typeof window !== 'undefined') {
      const hash = window.location.hash;
      const search = window.location.search;
      if (hash === '#documents') {
        return 'documents';
      }
      if (hash === '#dashboard' || search.includes('page=dashboard')) {
        return 'dashboard';
      }
    }
    return 'landing';
  });

  useEffect(() => {
    const handleHashChange = () => {
      if (window.location.hash === '#documents') {
        setRoute('documents');
      } else if (window.location.hash === '#dashboard') {
        setRoute('dashboard');
      } else if (!window.location.hash || window.location.hash === '#') {
        setRoute('landing');
      }
    };

    window.addEventListener('hashchange', handleHashChange);
    return () => window.removeEventListener('hashchange', handleHashChange);
  }, []);

  const navigateToDashboard = () => {
    setRoute('dashboard');
    if (typeof window !== 'undefined') {
      window.location.hash = 'dashboard';
    }
  };

  const navigateToLanding = () => {
    setRoute('landing');
    if (typeof window !== 'undefined') {
      window.location.hash = '';
    }
  };

  return (
    <Suspense fallback={<RouteFallback />}>
      {route === 'documents' && <DocumentsDemo onNavigateHome={navigateToLanding} />}
      {route === 'dashboard' && <Dashboard onNavigateHome={navigateToLanding} />}
      {route === 'landing' && <LandingPage onNavigateToDashboard={navigateToDashboard} />}
      <MemoryDevOverlay />
    </Suspense>
  );
}

export default App;