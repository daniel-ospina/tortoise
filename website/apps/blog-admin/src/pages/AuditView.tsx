/**
 * AuditView — audit trail for blog posts (plan W2/E2E-3: "audit view lists
 * published_by + ts"). Table of all posts with publish/review/create audit
 * fields.
 */

import { Loader2, ShieldCheck } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { useQuery } from '@tanstack/react-query';
import { navigate } from '@/hooks/useHashRoute';
import { listPosts, type BlogPostRow } from '@/lib/blog-api';

function formatDateTime(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

function statusBadge(status: BlogPostRow['status']) {
  switch (status) {
    case 'published': return <Badge variant="default">Published</Badge>;
    case 'draft': return <Badge variant="secondary">Draft</Badge>;
    case 'archived': return <Badge variant="outline">Archived</Badge>;
  }
}

export default function AuditView() {
  const { data: posts = [], isLoading } = useQuery({
    queryKey: ['admin', 'posts'],
    queryFn: listPosts,
  });

  return (
    <div className="max-w-6xl mx-auto px-4 py-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold font-serif flex items-center gap-2">
          <ShieldCheck className="w-6 h-6 text-primary" /> Audit Trail
        </h1>
        <button className="text-sm text-primary hover:underline" onClick={() => navigate('#/')}>
          ← Back to posts
        </button>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="w-6 h-6 animate-spin text-primary" />
        </div>
      ) : posts.length === 0 ? (
        <Card className="p-8 text-center text-muted-foreground">No posts yet.</Card>
      ) : (
        <div className="border rounded-lg overflow-hidden bg-card">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-secondary/50">
                <tr>
                  <th className="text-left p-3 font-medium">Title</th>
                  <th className="text-left p-3 font-medium">Status</th>
                  <th className="text-left p-3 font-medium">Created by</th>
                  <th className="text-left p-3 font-medium">Created at</th>
                  <th className="text-left p-3 font-medium">Published by</th>
                  <th className="text-left p-3 font-medium">Published at</th>
                  <th className="text-left p-3 font-medium">Reviewed by</th>
                  <th className="text-left p-3 font-medium">Reviewed at</th>
                </tr>
              </thead>
              <tbody>
                {posts.map(post => (
                  <tr key={post.id} className="border-t hover:bg-secondary/30 transition-colors">
                    <td className="p-3">
                      <button
                        className="font-medium text-left hover:text-primary transition-colors"
                        onClick={() => navigate(`#/edit/${post.id}`)}
                      >
                        {post.title}
                      </button>
                      <p className="text-xs text-muted-foreground font-mono">/{post.slug}</p>
                    </td>
                    <td className="p-3">{statusBadge(post.status)}</td>
                    <td className="p-3 text-muted-foreground font-mono text-xs">{post.created_by || '—'}</td>
                    <td className="p-3 text-muted-foreground text-xs">{formatDateTime(post.created_at)}</td>
                    <td className="p-3 text-muted-foreground font-mono text-xs">{post.published_by || '—'}</td>
                    <td className="p-3 text-muted-foreground text-xs">{formatDateTime(post.published_at)}</td>
                    <td className="p-3 text-muted-foreground font-mono text-xs">{post.reviewed_by || '—'}</td>
                    <td className="p-3 text-muted-foreground text-xs">{formatDateTime(post.reviewed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
