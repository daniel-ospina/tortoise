/**
 * App — blog admin SPA shell.
 *
 * Auth gate: useAuth checks the session (sb-tortoise-auth-token storage
 * adapter) + is_admin() membership; without a session it redirects to
 * https://tortoise.premiselabs.co/auth. The admin gate Function
 * (website/functions/admin/[[path]].ts) already verified the JWT server-side;
 * RLS is the data authorization boundary.
 *
 * Routes (hash-based): #/ list · #/new editor · #/edit/:id editor · #/audit.
 */

import { TooltipProvider } from '@/components/ui/tooltip';
import { useAuth } from '@/hooks/useAuth';
import { useHashRoute, navigate } from '@/hooks/useHashRoute';
import PostsList from '@/pages/PostsList';
import PostEditor from '@/pages/PostEditor';
import AuditView from '@/pages/AuditView';

function ShellHeader() {
  return (
    <header className="border-b border-border bg-card/60 backdrop-blur sticky top-0 z-40">
      <div className="max-w-6xl mx-auto px-4 h-14 flex items-center gap-6">
        <button
          className="font-bold font-serif text-lg text-foreground flex items-center gap-2"
          onClick={() => navigate('#/')}
        >
          <span className="text-primary font-mono text-xl">🐢</span> Tortoise Blog
        </button>
        <nav className="flex items-center gap-1 text-sm">
          <button
            className="px-3 py-1.5 rounded-md hover:bg-secondary transition-colors"
            onClick={() => navigate('#/')}
          >
            Posts
          </button>
          <button
            className="px-3 py-1.5 rounded-md hover:bg-secondary transition-colors"
            onClick={() => navigate('#/audit')}
          >
            Audit
          </button>
        </nav>
        <div className="ml-auto flex items-center gap-3">
          <a
            href="https://tortoise.premiselabs.co/blog"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-muted-foreground hover:text-primary transition-colors font-mono"
          >
            tortoise.premiselabs.co/blog ↗
          </a>
        </div>
      </div>
    </header>
  );
}

export default function App() {
  const { loading, session, isAdmin } = useAuth();
  const route = useHashRoute();

  if (loading || !session || !isAdmin) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <div className="text-3xl mb-3">🐢</div>
          <p className="text-muted-foreground font-mono text-sm">checking session…</p>
        </div>
      </div>
    );
  }

  const seg = route.segments;
  let view: React.ReactNode;
  if (seg[0] === 'new') {
    view = <PostEditor key="new" />;
  } else if (seg[0] === 'edit' && seg[1]) {
    view = <PostEditor key={`edit-${seg[1]}`} />;
  } else if (seg[0] === 'audit') {
    view = <AuditView />;
  } else {
    view = <PostsList />;
  }

  return (
    <TooltipProvider delayDuration={150}>
      <div className="min-h-screen bg-background text-foreground">
        <ShellHeader />
        <main className="pb-16">{view}</main>
      </div>
    </TooltipProvider>
  );
}
