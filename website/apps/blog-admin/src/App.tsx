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

import { useEffect, useRef, useState } from 'react';
import { TooltipProvider } from '@/components/ui/tooltip';
import {
  AlertDialog, AlertDialogContent, AlertDialogHeader, AlertDialogTitle,
  AlertDialogDescription, AlertDialogFooter, AlertDialogAction, AlertDialogCancel,
} from '@/components/ui/alert-dialog';
import { useAuth } from '@/hooks/useAuth';
import { useHashRoute, navigate } from '@/hooks/useHashRoute';
import { markAbandoned } from '@/lib/unsaved-guard';
import { isDirty } from '@/lib/unsaved-guard';
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

  // ── Unsaved-changes guard ────────────────────────────────────────────────
  // Dirty editor → beforeunload warning + hash-route interception. A nav away
  // from a dirty editor is reverted and a confirm dialog shown; the nav only
  // proceeds after explicit confirmation.
  const [pendingNav, setPendingNav] = useState<string | null>(null);
  const lastHash = useRef(window.location.hash || '#/');
  const confirmedNav = useRef(false);
  const reverting = useRef(false);

  useEffect(() => {
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (isDirty()) {
        e.preventDefault();
        e.returnValue = '';
      }
    };
    const onHashChange = () => {
      if (reverting.current) {
        reverting.current = false;
        return;
      }
      const next = window.location.hash || '#/';
      if (isDirty() && !confirmedNav.current) {
        const prev = lastHash.current;
        if (prev !== next) {
          // Revert the hash so the editor stays mounted; ask first.
          reverting.current = true;
          window.location.hash = prev;
        }
        setPendingNav(next);
        return;
      }
      lastHash.current = next;
      confirmedNav.current = false;
    };
    // Capture phase: run BEFORE useHashRoute's bubble listener so a dirty-route
    // change is reverted before the router reads the hash — the editor never
    // unmounts and its state survives the intercepted navigation.
    window.addEventListener('hashchange', onHashChange, true);
    return () => {
      window.removeEventListener('beforeunload', onBeforeUnload);
      window.removeEventListener('hashchange', onHashChange, true);
    };
  }, []);

  const confirmDiscard = () => {
    markAbandoned(); // save-in-flight nav guard: onSuccess must not yank back into the editor
    if (pendingNav) {
      confirmedNav.current = true;
      lastHash.current = pendingNav;
      window.location.hash = pendingNav;
    }
    setPendingNav(null);
  };

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

      {/* Unsaved-changes confirm (route intercept) */}
      <AlertDialog open={pendingNav !== null} onOpenChange={open => { if (!open) setPendingNav(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Discard unsaved changes?</AlertDialogTitle>
            <AlertDialogDescription>
              This post has unsaved changes. Leaving the editor will discard them.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep editing</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive/20 text-destructive border border-destructive/40 hover:bg-destructive/30"
              onClick={confirmDiscard}
            >
              Discard changes
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </TooltipProvider>
  );
}
