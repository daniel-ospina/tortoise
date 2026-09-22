/**
 * Minimal Supabase Database type for the blog admin SPA — blog_posts only.
 * Matches migration 20260827000001 (issue #1793). The admin SPA rides the
 * user's own PKCE session (RLS: is_admin() allowlist), never service-role.
 */

// NOTE: BlogPostRow is a `type` alias (NOT an interface) deliberately — TS only
// infers implicit index signatures for type aliases/object literals, and
// supabase-js's GenericTable requires Row to extend Record<string, unknown>.
// An interface here silently collapses the whole Database generic to `never`
// (every query result becomes never[]). See ElDato's client.ts comment for the
// sibling manifestation of this trap.
export type BlogPostRow = {
  id: string;
  slug: string;
  title: string;
  body: string; // markdown (canonical)
  excerpt: string | null;
  cover_image_url: string | null;
  tags: string[];
  author: string | null;
  status: 'draft' | 'published' | 'archived';
  meta_title: string | null;
  meta_description: string | null;
  published_at: string | null;
  published_by: string | null;
  created_by: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  hold_for_review: boolean;
  created_at: string;
  updated_at: string;
};

export type BlogPostInsert = Partial<Omit<BlogPostRow, 'id' | 'created_at' | 'updated_at'>>;
export type BlogPostUpdate = Partial<Omit<BlogPostRow, 'id' | 'created_at' | 'updated_at'>>;

export interface Database {
  public: {
    Tables: {
      blog_posts: {
        Row: BlogPostRow;
        Insert: BlogPostInsert;
        Update: BlogPostUpdate;
        Relationships: [];
      };
    };
    Views: Record<string, never>;
    Functions: Record<string, never>;
    Enums: Record<string, never>;
    CompositeTypes: Record<string, never>;
  };
}
