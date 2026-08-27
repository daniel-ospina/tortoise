/**
 * PostsList — admin list of blog posts with tabs, review queue, and
 * per-state actions.
 *
 * PORTED from ElDato's GuidesList (issue #1798) and adapted to the blog
 * review workflow (plan W2): the Review Queue tab surfaces three item kinds —
 * agent drafts (status=draft), unreviewed direct-published
 * (published AND reviewed_at IS NULL), and held (hold_for_review=true).
 *
 * Actions (per state): publish, mark reviewed, edit, request changes (with
 * note), unpublish, archive, clear hold. Unpublish / archive / request
 * changes are human-only gates (plan W2) — confirmed via dialogs.
 *
 * Attribution: ported from eldato/src/pages/admin/GuidesList.tsx
 * (private repo, 2026-08).
 */

import { useMemo, useState } from 'react';
import {
  Plus, Pencil, Loader2, FileText, ClipboardCheck, ExternalLink,
  Archive, Ban, MessageSquareWarning, RefreshCcw,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter, DialogTrigger } from '@/components/ui/dialog';
import { AlertDialog, AlertDialogContent, AlertDialogHeader, AlertDialogTitle, AlertDialogDescription, AlertDialogFooter, AlertDialogAction, AlertDialogCancel, AlertDialogTrigger } from '@/components/ui/alert-dialog';
import { Textarea } from '@/components/ui/textarea';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { useAuth } from '@/hooks/useAuth';
import { navigate } from '@/hooks/useHashRoute';
import {
  listPosts, listQueue, publishPost, markReviewedPost, unpublishPost,
  archivePost, requestChangesPost, clearHoldPost, queueKind,
  type BlogPostRow,
} from '@/lib/blog-api';

type TabKey = 'queue' | 'all' | 'draft' | 'published' | 'archived';

function statusBadge(status: BlogPostRow['status']) {
  switch (status) {
    case 'published': return <Badge variant="default">Published</Badge>;
    case 'draft': return <Badge variant="secondary">Draft</Badge>;
    case 'archived': return <Badge variant="outline">Archived</Badge>;
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

/** Confirm-dialog wrapper for human-gated actions (W2). */
function ConfirmAction({
  trigger,
  title,
  description,
  confirmLabel,
  onConfirm,
  destructive,
}: {
  trigger: React.ReactNode;
  title: string;
  description: string;
  confirmLabel: string;
  onConfirm: () => void;
  destructive?: boolean;
}) {
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>{trigger}</AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            className={destructive ? 'bg-destructive/20 text-destructive border border-destructive/40 hover:bg-destructive/30' : ''}
            onClick={onConfirm}
          >
            {confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function RowActions({ post }: { post: BlogPostRow }) {
  const queryClient = useQueryClient();
  const { session } = useAuth();
  const userId = session?.user?.id ?? '';
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState('');

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['admin', 'posts'] });
    queryClient.invalidateQueries({ queryKey: ['admin', 'queue'] });
  };

  const mutation = (fn: () => Promise<unknown>, successMsg: string) =>
    useMutation({
      mutationFn: fn,
      onSuccess: () => { invalidate(); toast.success(successMsg); },
      onError: (err) => toast.error(err instanceof Error ? err.message : 'Action failed'),
    });

  const doPublish = mutation(() => publishPost(post, { userId }), 'Published');
  const doMarkReviewed = mutation(() => markReviewedPost(post, { userId }), 'Marked as reviewed');
  const doUnpublish = mutation(() => unpublishPost(post), 'Unpublished');
  const doArchive = mutation(() => archivePost(post), 'Archived');
  const doClearHold = mutation(() => clearHoldPost(post), 'Hold cleared');
  const doRequestChanges = mutation(async () => {
    if (!note.trim()) {
      toast.error('A review note is required — tell the agent what to change');
      throw new Error('empty note');
    }
    await requestChangesPost(post, note);
    setNoteOpen(false);
    setNote('');
  }, 'Changes requested');

  const iconBtn = 'h-8 w-8';

  return (
    <div className="flex items-center justify-end gap-1">
      {post.status === 'draft' && (
        <Button size="sm" className="h-8" onClick={() => doPublish.mutate()} disabled={doPublish.isPending}>
          {doPublish.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ExternalLink className="w-3.5 h-3.5 mr-1" />}
          Publish
        </Button>
      )}

      {post.status === 'published' && !post.reviewed_at && (
        <Button size="sm" variant="outline" className="h-8" onClick={() => doMarkReviewed.mutate()} disabled={doMarkReviewed.isPending}>
          {doMarkReviewed.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ClipboardCheck className="w-3.5 h-3.5 mr-1" />}
          Mark reviewed
        </Button>
      )}

      <Dialog open={noteOpen} onOpenChange={setNoteOpen}>
        <DialogTrigger asChild>
          <Button size="sm" variant="outline" className="h-8">
            <MessageSquareWarning className="w-3.5 h-3.5 mr-1" />
            Request changes
          </Button>
        </DialogTrigger>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Request changes</DialogTitle>
            <DialogDescription>
              Sends the post back to draft with a note. The agent reads the note and rewrites via the publish API.
            </DialogDescription>
          </DialogHeader>
          <Textarea
            value={note}
            onChange={e => setNote(e.target.value)}
            placeholder="What needs to change? e.g. verify the pricing claim, tighten the intro, add a table…"
            className="min-h-[120px]"
            autoFocus
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setNoteOpen(false)}>Cancel</Button>
            <Button onClick={() => doRequestChanges.mutate()} disabled={doRequestChanges.isPending || !note.trim()}>
              {doRequestChanges.isPending && <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />}
              Send request
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {post.status !== 'archived' && (
        <Button variant="ghost" size="icon" className={iconBtn} title="Edit" onClick={() => navigate(`#/edit/${post.id}`)}>
          <Pencil className="w-4 h-4" />
        </Button>
      )}

      {post.status === 'published' && (
        <a href={`https://tortoise.premiselabs.co/blog/${post.slug}`} target="_blank" rel="noopener noreferrer">
          <Button variant="ghost" size="icon" className={iconBtn} title="View public post">
            <ExternalLink className="w-4 h-4" />
          </Button>
        </a>
      )}

      {post.hold_for_review && post.status !== 'archived' && (
        <ConfirmAction
          trigger={
            <Button variant="ghost" size="icon" className={iconBtn} title="Clear hold (make public)">
              <RefreshCcw className="w-4 h-4" />
            </Button>
          }
          title="Clear hold for review?"
          description="The post will become visible on all public surfaces (index, article, sitemap, feed) wherever its status allows. E2E-2 after-clear step."
          confirmLabel="Clear hold"
          onConfirm={() => doClearHold.mutate()}
        />
      )}

      {post.status === 'published' && (
        <ConfirmAction
          trigger={
            <Button variant="ghost" size="icon" className={`${iconBtn} text-destructive`} title="Unpublish">
              <Ban className="w-4 h-4" />
            </Button>
          }
          title="Unpublish this post?"
          description="Removes it from the public blog immediately. Republishing later re-enters the review queue (reviewed_at reset)."
          confirmLabel="Unpublish"
          destructive
          onConfirm={() => doUnpublish.mutate()}
        />
      )}

      {post.status !== 'archived' && (
        <ConfirmAction
          trigger={
            <Button variant="ghost" size="icon" className={`${iconBtn} text-destructive`} title="Archive">
              <Archive className="w-4 h-4" />
            </Button>
          }
          title="Archive this post?"
          description="Archiving is terminal — the post cannot be un-archived or transitioned to any other status."
          confirmLabel="Archive"
          destructive
          onConfirm={() => doArchive.mutate()}
        />
      )}
    </div>
  );
}

function PostRow({ post, showQueueKind }: { post: BlogPostRow; showQueueKind?: boolean }) {
  const kind = showQueueKind ? queueKind(post) : null;
  return (
    <tr className="border-t hover:bg-secondary/30 transition-colors">
      <td className="p-3">
        <div className="flex items-center gap-2">
          <button
            className="font-medium text-left hover:text-primary transition-colors"
            onClick={() => navigate(`#/edit/${post.id}`)}
          >
            {post.title}
          </button>
          {kind && (
            <Badge
              variant={kind === 'agent-draft' ? 'secondary' : 'warning'}
              className="shrink-0"
            >
              {kind === 'agent-draft' ? 'Agent draft' : kind === 'unreviewed-published' ? 'Unreviewed' : 'Held'}
            </Badge>
          )}
        </div>
        {post.review_note && (
          <p className="text-xs text-warning mt-1 flex items-center gap-1 max-w-md truncate">
            <MessageSquareWarning className="w-3 h-3 shrink-0" />
            {post.review_note}
          </p>
        )}
      </td>
      <td className="p-3">{statusBadge(post.status)}</td>
      <td className="p-3 text-muted-foreground font-mono text-xs">
        {post.created_by || '—'}
      </td>
      <td className="p-3 text-muted-foreground">
        {formatDate(post.published_at)}
      </td>
      <td className="p-3">
        {post.reviewed_at ? (
          <Badge variant="success">Reviewed</Badge>
        ) : post.hold_for_review ? (
          <Badge variant="warning">Held</Badge>
        ) : (
          <span className="text-muted-foreground/60 text-xs">—</span>
        )}
      </td>
      <td className="p-3">
        <RowActions post={post} />
      </td>
    </tr>
  );
}

export default function PostsList() {
  const { data: posts = [], isLoading } = useQuery({
    queryKey: ['admin', 'posts'],
    queryFn: listPosts,
  });
  const { data: queue = [], isLoading: queueLoading } = useQuery({
    queryKey: ['admin', 'queue'],
    queryFn: listQueue,
  });

  const tabs = useMemo(() => {
    const byStatus = (s: BlogPostRow['status']) => posts.filter(p => p.status === s);
    return {
      queue,
      all: posts,
      draft: byStatus('draft'),
      published: byStatus('published'),
      archived: byStatus('archived'),
    } satisfies Record<TabKey, BlogPostRow[]>;
  }, [posts, queue]);

  const count = (key: TabKey) => tabs[key].length;

  return (
    <div className="max-w-6xl mx-auto px-4 py-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold font-serif flex items-center gap-2">
          <FileText className="w-6 h-6 text-primary" /> Blog Posts
        </h1>
        <Button onClick={() => navigate('#/new')}>
          <Plus className="w-4 h-4 mr-1.5" /> New Post
        </Button>
      </div>

      <Tabs defaultValue="queue">
        <TabsList>
          <TabsTrigger value="queue">Review Queue {count('queue') > 0 && <span className="text-primary font-semibold">({count('queue')})</span>}</TabsTrigger>
          <TabsTrigger value="all">All ({count('all')})</TabsTrigger>
          <TabsTrigger value="draft">Draft ({count('draft')})</TabsTrigger>
          <TabsTrigger value="published">Published ({count('published')})</TabsTrigger>
          <TabsTrigger value="archived">Archived ({count('archived')})</TabsTrigger>
        </TabsList>

        {(['queue', 'all', 'draft', 'published', 'archived'] as TabKey[]).map(key => (
          <TabsContent key={key} value={key}>
            {isLoading || (key === 'queue' && queueLoading) ? (
              <div className="flex justify-center py-12">
                <Loader2 className="w-6 h-6 animate-spin text-primary" />
              </div>
            ) : tabs[key].length === 0 ? (
              <Card className="p-8 text-center text-muted-foreground">
                {key === 'queue'
                  ? 'Review queue is empty — agent drafts, unreviewed publishes, and held posts will appear here.'
                  : 'No posts in this view.'}
              </Card>
            ) : (
              <div className="border rounded-lg overflow-hidden bg-card">
                <table className="w-full text-sm">
                  <thead className="bg-secondary/50">
                    <tr>
                      <th className="text-left p-3 font-medium">Title</th>
                      <th className="text-left p-3 font-medium">Status</th>
                      <th className="text-left p-3 font-medium">Author / Agent</th>
                      <th className="text-left p-3 font-medium">Published</th>
                      <th className="text-left p-3 font-medium">Review</th>
                      <th className="text-right p-3 font-medium">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tabs[key].map(post => (
                      <PostRow key={post.id} post={post} showQueueKind={key === 'queue'} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </TabsContent>
        ))}
      </Tabs>
    </div>
  );
}
