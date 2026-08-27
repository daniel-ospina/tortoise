/**
 * PostEditor — admin page for creating/editing blog posts with TipTap.
 *
 * PORTED from ElDato's GuideEditor (issue #1798, owner-validated editor UX),
 * adapted to the blog_posts schema:
 *   slug / title / body(markdown) / excerpt / cover_image_url / tags /
 *   meta_title / meta_description / status / hold_for_review / review_note
 *
 * STRIPPED: deal embeds, carousels, FAQs, columns, related-deal filters,
 * SEO-generation RPC (no guide-specific hooks on the blog surface).
 * ADDED: markdown import/export (tiptap-markdown — the canonical body format
 * is markdown; the editor imports on load and exports on save), tags as
 * text[] (comma-separated input), hold_for_review switch, review_note banner.
 *
 * Attribution: ported from eldato/src/pages/admin/GuideEditor.tsx
 * (private repo, 2026-08).
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { useEditor, EditorContent } from '@tiptap/react';
import { Save, Loader2, ArrowLeft, ExternalLink, Upload, X, MessageSquareWarning } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { Card } from '@/components/ui/card';
import { Switch } from '@/components/ui/switch';
import { Badge } from '@/components/ui/badge';
import {
  AlertDialog, AlertDialogContent, AlertDialogHeader, AlertDialogTitle,
  AlertDialogDescription, AlertDialogFooter, AlertDialogAction, AlertDialogCancel,
} from '@/components/ui/alert-dialog';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { useAuth } from '@/hooks/useAuth';
import { useHashRoute, navigate } from '@/hooks/useHashRoute';
import { wasAbandoned, resetAbandoned } from '@/lib/unsaved-guard';
import {
  getPost, uploadBlogImage, deleteBlogImage, updatePost,
  isValidSlug, tagsError,
  type BlogPostRow,
} from '@/lib/blog-api';
import { createSaveHandler } from '@/lib/save';
import { setDirty } from '@/lib/unsaved-guard';
import { buildEditorExtensions, editorToMarkdown } from '@/lib/markdown';
import { BlogEditorToolbar } from '@/components/guides/BlogEditorToolbar';

interface PostFormData {
  title: string;
  slug: string;
  excerpt: string;
  cover_image_url: string;
  tags: string;
  meta_title: string;
  meta_description: string;
  hold_for_review: boolean;
}

const INITIAL_FORM: PostFormData = {
  title: '',
  slug: '',
  excerpt: '',
  cover_image_url: '',
  tags: '',
  meta_title: '',
  meta_description: '',
  hold_for_review: false,
};

function slugify(text: string): string {
  return text
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9\s-]/g, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 100);
}

/** Read-only status badge synced from the current row (the save buttons are the status controls). */
function statusBadge(status: BlogPostRow['status']) {
  switch (status) {
    case 'published': return <Badge variant="default">Published</Badge>;
    case 'draft': return <Badge variant="secondary">Draft</Badge>;
    case 'archived': return <Badge variant="outline">Archived</Badge>;
  }
}

export default function PostEditor() {
  const { segments } = useHashRoute();
  const isNew = segments[0] !== 'edit';
  const id = segments[1];
  const navigateBack = () => navigate('#/');
  const queryClient = useQueryClient();
  const { session } = useAuth();

  const [form, setForm] = useState<PostFormData>(INITIAL_FORM);
  const [saving, setSaving] = useState(false);
  const [inTable, setInTable] = useState(false);
  const [confirmUnpublish, setConfirmUnpublish] = useState(false);
  const slugTouched = useRef(false);
  // Hydration setContent must not mark the post dirty.
  const ignoreDirtyRef = useRef(true);

  // TipTap editor (markdown import/export via tiptap-markdown)
  const editor = useEditor({
    extensions: buildEditorExtensions(),
    onSelectionUpdate: ({ editor: e }) => {
      setInTable(e.isActive('table'));
    },
    onTransaction: ({ editor: e }) => {
      setInTable(e.isActive('table'));
    },
    onUpdate: () => {
      if (!ignoreDirtyRef.current) setDirty(true);
    },
  });

  // Refs mirror the CURRENT render's form/editor so the save mutation reads
  // state at execution time, never from a stale closure (PR #1818 P2).
  const formRef = useRef(form);
  formRef.current = form;
  const editorRef = useRef(editor);
  editorRef.current = editor;

  // Load existing post
  const { data: existingPost, isLoading: loadingPost } = useQuery({
    queryKey: ['admin', 'post', id],
    queryFn: async () => {
      if (isNew) return null;
      return getPost(id!);
    },
    enabled: !isNew,
  });
  const isArchived = existingPost?.status === 'archived';

  // Archived posts are read-only (terminal) — editor editable follows the row.
  useEffect(() => {
    if (editor) editor.setEditable(!isArchived);
  }, [editor, isArchived]);

  // Hydrate form + editor from the DB row — hydration guard so background
  // refetches don't wipe user edits (ported pattern from GuideEditor).
  const [postHydrated, setPostHydrated] = useState(false);
  if (existingPost && editor && !postHydrated) {
    setPostHydrated(true);
    setForm({
      title: existingPost.title || '',
      slug: existingPost.slug || '',
      excerpt: existingPost.excerpt || '',
      cover_image_url: existingPost.cover_image_url || '',
      tags: (existingPost.tags || []).join(', '),
      meta_title: existingPost.meta_title || '',
      meta_description: existingPost.meta_description || '',
      hold_for_review: existingPost.hold_for_review || false,
    });
    slugTouched.current = true;
  }

  // Import markdown into the editor once hydration flips. post.body is
  // markdown (canonical) — tiptap-markdown parses it to the TipTap doc.
  useEffect(() => {
    if (postHydrated && editor && existingPost) {
      editor.commands.setContent(existingPost.body || '');
    }
    if (postHydrated) ignoreDirtyRef.current = false;
    // Run once when hydration just flipped — body updates from background
    // refetches must NOT overwrite user edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [postHydrated]);

  // Clear the shared dirty flag when the editor unmounts (route change completes).
  useEffect(() => {
    return () => setDirty(false);
  }, []);

  // Auto-slug from title (only on new posts with untouched slug)
  useEffect(() => {
    if (isNew && !slugTouched.current && form.title) {
      setForm(prev => ({ ...prev, slug: slugify(prev.title) }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.title]);

  const updateField = useCallback((field: keyof PostFormData, value: PostFormData[keyof PostFormData]) => {
    setForm(prev => ({ ...prev, [field]: value }));
    setDirty(true);
  }, []);

  // ── Image uploads ────────────────────────────────────────────────────────

  /** Inline image: file picker → upload to blog-images/{slug}/ → insert. */
  async function handleInlineImageUpload() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/jpeg,image/png,image/webp';
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file || !editor) return;
      try {
        const slug = form.slug || 'draft';
        const publicUrl = await uploadBlogImage(file, slug);
        editor.chain().focus().setImage({ src: publicUrl }).run();
        toast.success('Image inserted');
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Image upload failed');
      }
    };
    input.click();
  }

  const [uploadingCover, setUploadingCover] = useState(false);

  async function handleCoverUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = ''; // allow re-selecting the same file
    setUploadingCover(true);
    try {
      const slug = form.slug || 'draft';
      const publicUrl = await uploadBlogImage(file, slug);
      updateField('cover_image_url', publicUrl);
      toast.success('Cover image uploaded');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Cover upload failed');
    } finally {
      setUploadingCover(false);
    }
  }

  async function handleRemoveCover() {
    if (!form.cover_image_url) return;
    try {
      await deleteBlogImage(form.cover_image_url);
    } catch {
      // orphaned object is acceptable — the URL is cleared regardless
    }
    updateField('cover_image_url', '');
  }

  // ── Save ─────────────────────────────────────────────────────────────────

  const saveMutation = useMutation({
    mutationFn: (mode: 'draft' | 'publish') => {
      if (!session?.user?.id) throw new Error('No session');
      // Reads the CURRENT form + editor body via refs at execution time
      // (stale-closure guard).
      return createSaveHandler(() => ({
        form: formRef.current,
        editorBody: editorRef.current ? editorToMarkdown(editorRef.current) : '',
        userId: session.user.id,
        isNew,
        id,
      }))(mode);
    },
    onSuccess: (savedId) => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'posts'] });
      queryClient.invalidateQueries({ queryKey: ['admin', 'post', savedId] });
      toast.success(isNew ? 'Post created' : 'Post saved');
      setDirty(false);
      if (isNew) {
        if (!wasAbandoned()) navigate(`#/edit/${savedId}`);
        resetAbandoned(); // self-heal: clear the flag so the next create navigates normally
      }
    },
    onError: (err) => {
      toast.error(`Save failed: ${err instanceof Error ? err.message : 'Unknown error'}`);
    },
  });

  function validateForm(): boolean {
    if (!form.title.trim()) {
      toast.error('Title is required');
      return false;
    }
    if (form.title.trim().length > 200) {
      toast.error('Title must be 200 characters or fewer');
      return false;
    }
    if (!form.slug.trim()) {
      toast.error('Slug is required');
      return false;
    }
    if (!isValidSlug(form.slug)) {
      toast.error('Slug must be lowercase letters/numbers separated by dashes (max 100 chars)');
      return false;
    }
    if (form.excerpt.trim().length > 300) {
      toast.error('Excerpt must be 300 characters or fewer');
      return false;
    }
    return true;
  }

  async function doSave(mode: 'draft' | 'publish') {
    setSaving(true);
    try {
      await saveMutation.mutateAsync(mode);
    } finally {
      setSaving(false);
    }
  }

  async function handleSave(mode: 'draft' | 'publish') {
    if (isArchived) return; // terminal — never allow saves on archived
    if (!validateForm()) return;

    // Client-side tag cap — matches the API contract (max 10).
    const tagErr = tagsError(form.tags);
    if (tagErr) {
      toast.error(tagErr);
      return;
    }

    // Human gate: saving a published post as draft unpublishes it (W2).
    // Confirmed via AlertDialog (not window.confirm).
    if (mode === 'draft' && existingPost?.status === 'published') {
      setConfirmUnpublish(true);
      return;
    }

    await doSave(mode);
  }

  /** Clear a request-changes note after addressing it (completes W2 loop). */
  const clearNoteMutation = useMutation({
    mutationFn: async () => {
      await updatePost(id!, { review_note: null });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'post', id] });
      toast.success('Review note cleared');
    },
    onError: () => toast.error('Failed to clear review note'),
  });

  if (loadingPost) {
    return (
      <div className="flex justify-center py-20">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
      </div>
    );
  }

  if (!isNew && !existingPost) {
    return (
      <div className="max-w-3xl mx-auto px-4 py-20 text-center">
        <p className="text-muted-foreground mb-4">Post not found.</p>
        <Button variant="outline" onClick={navigateBack}>
          <ArrowLeft className="w-4 h-4 mr-1.5" /> Back to posts
        </Button>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="icon" onClick={navigateBack} aria-label="Back to posts">
            <ArrowLeft className="w-5 h-5" />
          </Button>
          <h1 className="text-xl font-bold font-serif">{isNew ? 'New Post' : 'Edit Post'}</h1>
        </div>
        <div className="flex gap-2">
          {existingPost?.status === 'published' && (
            <a href={`https://tortoise.premiselabs.co/blog/${existingPost.slug}`} target="_blank" rel="noopener noreferrer">
              <Button variant="outline">
                <ExternalLink className="w-4 h-4 mr-1.5" />
                View
              </Button>
            </a>
          )}
          {isArchived ? (
            <div className="text-sm text-muted-foreground border border-amber-500/30 bg-amber-500/5 rounded-md px-3 py-2">
              <strong>Archived</strong> — archive is terminal. This post is read-only.
            </div>
          ) : (
            <>
              <Button variant="outline" onClick={() => handleSave('draft')} disabled={saving}>
                {saving ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Save className="w-4 h-4 mr-1.5" />}
                Save as draft
              </Button>
              <Button onClick={() => handleSave('publish')} disabled={saving}>
                {saving ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <ExternalLink className="w-4 h-4 mr-1.5" />}
                {existingPost?.status === 'published' ? 'Save & Republish' : 'Publish'}
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Request-changes note (W2): show prominently if one came back */}
      {existingPost?.review_note && (
        <div className="mb-6 rounded-lg border border-warning/30 bg-warning/10 px-4 py-3 flex items-start gap-3">
          <MessageSquareWarning className="w-5 h-5 text-warning shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="text-sm font-semibold text-warning">Change request from review</p>
            <p className="text-sm mt-0.5">{existingPost.review_note}</p>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => clearNoteMutation.mutate()}
            disabled={clearNoteMutation.isPending}
            title="Dismiss this note"
          >
            Dismiss
          </Button>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_320px] gap-6">
        {/* Main: Editor */}
        <div>
          <Input
            value={form.title}
            onChange={e => updateField('title', e.target.value)}
            placeholder="Post title"
            className="text-xl font-semibold mb-4 h-12 bg-background/40"
          />

          <Card className="max-h-[calc(100vh-14rem)] overflow-y-auto rounded-xl">
            <BlogEditorToolbar editor={editor} onInsertImage={handleInlineImageUpload} inTable={inTable} />
            <EditorContent
              editor={editor}
              className="prose prose-sm max-w-none pl-4 md:pl-10 pr-4 py-4 min-h-[400px] prose-invert focus-within:outline-none [&_.ProseMirror]:outline-none [&_.ProseMirror]:min-h-[380px] [&_table]:border-collapse [&_table]:w-full [&_td]:border [&_td]:border-border [&_td]:p-2 [&_th]:border [&_th]:border-border [&_th]:p-2 [&_th]:bg-secondary/60 [&_th]:font-semibold"
            />
          </Card>
        </div>

        {/* Sidebar: Metadata */}
        <div className="space-y-4">
          <Card className="p-4 space-y-4 rounded-xl">
            <div>
              <Label htmlFor="slug">Slug</Label>
              <Input
                id="slug"
                value={form.slug}
                disabled={!isNew}
                onChange={e => { slugTouched.current = true; updateField('slug', e.target.value); }}
                placeholder="tortoise-memory-engine"
                className={!isNew ? 'opacity-60 cursor-not-allowed' : ''}
              />
              <p className="text-xs text-muted-foreground mt-1">
                {isNew ? '/blog/' : 'Immutable after create — /blog/'}
                <span className="text-primary">{form.slug || '...'}</span>
              </p>
            </div>

            <div>
              <Label htmlFor="excerpt">Excerpt</Label>
              <Textarea
                id="excerpt"
                value={form.excerpt}
                onChange={e => updateField('excerpt', e.target.value)}
                placeholder="One-to-two sentence summary shown in cards and search results"
                maxLength={300}
                className="resize-none"
              />
              <p className="text-xs text-muted-foreground mt-1">{form.excerpt.length}/300</p>
            </div>

            <div>
              <Label htmlFor="tags">Tags</Label>
              <Input
                id="tags"
                value={form.tags}
                onChange={e => updateField('tags', e.target.value)}
                placeholder="memory, graph, agents"
              />
              <p className="text-xs text-muted-foreground mt-1">Comma-separated (max 10)</p>
            </div>
          </Card>

          <Card className="p-4 space-y-4 rounded-xl">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold text-sm">SEO</h3>
              <span className="text-xs text-muted-foreground">Overrides default title/description</span>
            </div>
            <div>
              <Label htmlFor="meta_title">Meta Title</Label>
              <Input
                id="meta_title"
                value={form.meta_title}
                onChange={e => updateField('meta_title', e.target.value)}
                placeholder={form.title || 'Auto-generated from title'}
                maxLength={60}
              />
              <p className="text-xs text-muted-foreground mt-1">{form.meta_title.length}/60</p>
            </div>
            <div>
              <Label htmlFor="meta_description">Meta Description</Label>
              <Input
                id="meta_description"
                value={form.meta_description}
                onChange={e => updateField('meta_description', e.target.value)}
                placeholder="Brief description for search results"
                maxLength={155}
              />
              <p className="text-xs text-muted-foreground mt-1">{form.meta_description.length}/155</p>
            </div>
          </Card>

          <Card className="p-4 space-y-3 rounded-xl">
            <Label className="font-semibold text-sm">Cover Image</Label>
            {form.cover_image_url ? (
              <div className="relative">
                <img
                  src={form.cover_image_url}
                  alt="Cover"
                  className="w-full aspect-video object-cover rounded-lg"
                />
                <button
                  type="button"
                  onClick={handleRemoveCover}
                  className="absolute top-2 right-2 p-1 bg-destructive text-white rounded-full hover:bg-destructive/90 transition-colors"
                  aria-label="Remove cover"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            ) : (
              <div className="w-full aspect-video bg-secondary/60 rounded-lg flex items-center justify-center">
                <Upload className="w-8 h-8 text-muted-foreground" />
              </div>
            )}
            <div>
              <input
                type="file"
                accept="image/jpeg,image/png,image/webp"
                onChange={handleCoverUpload}
                className="hidden"
                id="cover-image-upload"
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="w-full"
                disabled={uploadingCover}
                onClick={() => document.getElementById('cover-image-upload')?.click()}
              >
                {uploadingCover ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Upload className="w-3.5 h-3.5 mr-1" />}
                {uploadingCover ? 'Uploading...' : 'Upload cover'}
              </Button>
              <p className="text-xs text-muted-foreground mt-1">JPEG/PNG/WebP, max 5MB. Used as OG image.</p>
            </div>
          </Card>

          <Card className="p-4 space-y-4 rounded-xl">
            <div className="flex items-center justify-between">
              <Label htmlFor="status">Status</Label>
              {/* Read-only — Save-as-draft / Publish buttons are the status controls */}
              <div className="flex items-center gap-2">
                {statusBadge(existingPost?.status ?? 'draft')}
                {form.hold_for_review && <Badge variant="warning">Held</Badge>}
              </div>
            </div>
            <div className="flex items-center justify-between">
              <div>
                <Label htmlFor="hold" className="font-semibold">Hold for review</Label>
                <p className="text-xs text-muted-foreground mt-0.5">Keeps the post private everywhere until cleared (W3)</p>
              </div>
              <Switch
                id="hold"
                checked={form.hold_for_review}
                onCheckedChange={v => updateField('hold_for_review', v)}
              />
            </div>
            {(existingPost?.status ?? 'draft') === 'published' && existingPost?.slug && (
              <a
                href={`https://tortoise.premiselabs.co/blog/${existingPost.slug}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-primary hover:underline mt-2 block truncate"
              >
                https://tortoise.premiselabs.co/blog/{existingPost.slug}
              </a>
            )}
            {(existingPost?.status ?? 'draft') === 'draft' && (
              <p className="text-xs text-muted-foreground mt-1">Draft — not visible to the public blog</p>
            )}
          </Card>
        </div>
      </div>

      {/* Unpublish human gate (W2) — AlertDialog replaces the native confirm */}
      <AlertDialog open={confirmUnpublish} onOpenChange={setConfirmUnpublish}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Unpublish this post?</AlertDialogTitle>
            <AlertDialogDescription>
              This post is currently published. Saving as draft will UNPUBLISH it and remove it from the public blog. Continue?
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive/20 text-destructive border border-destructive/40 hover:bg-destructive/30"
              onClick={() => {
                setConfirmUnpublish(false);
                void doSave('draft');
              }}
            >
              Unpublish & save as draft
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
