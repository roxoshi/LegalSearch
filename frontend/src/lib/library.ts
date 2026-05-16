import { getApiUrl } from './api';

// ─── Shared ───────────────────────────────────────────────────────────────────

export interface CrossRef {
  target_type: string;
  target_id: number;
  anchor_text: string | null;
  label: string | null;
  title: string | null;
}

export interface SearchHit {
  type: string;
  primary_id: number;
  label: string;
  title: string | null;
  snippet: string | null;
}

// ─── Document shapes ──────────────────────────────────────────────────────────

export interface ActListItem {
  id: number;
  primary_id: number;
  act_name: string;
  chapter_no: string | null;
  chapter_name: string | null;
  section_no: string;
  section_name: string | null;
}

export interface RuleListItem {
  id: number;
  primary_id: number;
  act_name: string;
  section_no: string;
  section_name: string | null;
}

export interface NotificationListItem {
  id: number;
  primary_id: number;
  notification_no: string;
  title: string | null;
  year: number | null;
  category: string | null;
  is_active: boolean;
}

export interface CircularListItem {
  id: number;
  primary_id: number;
  circular_no: string;
  subject: string | null;
  year: number | null;
  category: string | null;
  is_active: boolean;
}

export interface ActDetail extends ActListItem {
  content_id: number;
  content: string | null;
  html_content: string | null;
  source_url: string | null;
  cross_references: CrossRef[];
}

export interface RuleDetail {
  id: number;
  primary_id: number;
  content_id: number;
  act_name: string;
  chapter_id: number | null;
  section_no: string;
  section_name: string | null;
  content: string | null;
  html_content: string | null;
  source_url: string | null;
  cross_references: CrossRef[];
}

export interface NotificationDetail {
  id: number;
  primary_id: number;
  content_id: number;
  notification_no: string;
  issued_on: string | null;
  title: string | null;
  content: string | null;
  category: string | null;
  year: number | null;
  is_active: boolean;
  is_amended: boolean;
}

export interface CircularDetail {
  id: number;
  primary_id: number;
  content_id: number;
  circular_no: string;
  issued_on: string | null;
  subject: string | null;
  content: string | null;
  category: string | null;
  year: number | null;
  is_active: boolean;
  is_amended: boolean;
}

export type LibraryDoc =
  | { type: 'act'; document: ActDetail }
  | { type: 'rule'; document: RuleDetail }
  | { type: 'notification'; document: NotificationDetail }
  | { type: 'circular'; document: CircularDetail };

// ─── API calls ────────────────────────────────────────────────────────────────

export async function fetchActs(params?: {
  act_name?: string;
  chapter_no?: string;
  limit?: number;
}): Promise<ActListItem[]> {
  const p = new URLSearchParams();
  if (params?.act_name) p.set('act_name', params.act_name);
  if (params?.chapter_no) p.set('chapter_no', params.chapter_no);
  if (params?.limit) p.set('limit', String(params.limit));
  const res = await fetch(`${getApiUrl()}/library/acts?${p}`);
  if (!res.ok) throw new Error('Failed to fetch acts');
  return res.json();
}

export async function fetchRules(params?: {
  act_name?: string;
  limit?: number;
}): Promise<RuleListItem[]> {
  const p = new URLSearchParams();
  if (params?.act_name) p.set('act_name', params.act_name);
  if (params?.limit) p.set('limit', String(params.limit));
  const res = await fetch(`${getApiUrl()}/library/rules?${p}`);
  if (!res.ok) throw new Error('Failed to fetch rules');
  return res.json();
}

export async function fetchNotifications(params?: {
  year?: number;
  category?: string;
  limit?: number;
}): Promise<NotificationListItem[]> {
  const p = new URLSearchParams();
  if (params?.year) p.set('year', String(params.year));
  if (params?.category) p.set('category', params.category);
  if (params?.limit) p.set('limit', String(params.limit));
  const res = await fetch(`${getApiUrl()}/library/notifications?${p}`);
  if (!res.ok) throw new Error('Failed to fetch notifications');
  return res.json();
}

export async function searchNotifications(q: string, limit = 20): Promise<NotificationListItem[]> {
  const p = new URLSearchParams({ q, limit: String(limit) });
  const res = await fetch(`${getApiUrl()}/library/notifications/search?${p}`);
  if (!res.ok) throw new Error('Search failed');
  return res.json();
}

export async function searchCirculars(q: string, limit = 20): Promise<CircularListItem[]> {
  const p = new URLSearchParams({ q, limit: String(limit) });
  const res = await fetch(`${getApiUrl()}/library/circulars/search?${p}`);
  if (!res.ok) throw new Error('Search failed');
  return res.json();
}

export async function fetchCirculars(params?: {
  year?: number;
  category?: string;
  limit?: number;
}): Promise<CircularListItem[]> {
  const p = new URLSearchParams();
  if (params?.year) p.set('year', String(params.year));
  if (params?.category) p.set('category', params.category);
  if (params?.limit) p.set('limit', String(params.limit));
  const res = await fetch(`${getApiUrl()}/library/circulars?${p}`);
  if (!res.ok) throw new Error('Failed to fetch circulars');
  return res.json();
}

/** Fetches a single document by type + primary_id via the generic resolver. */
export async function resolveLibraryDoc(
  type: string,
  primaryId: number,
): Promise<LibraryDoc> {
  const res = await fetch(`${getApiUrl()}/library/resolve/${type}/${primaryId}`);
  if (!res.ok) throw new Error(`Not found: ${type}/${primaryId}`);
  return res.json();
}

export async function librarySearch(
  q: string,
  types?: string,
  limit = 20,
): Promise<SearchHit[]> {
  const p = new URLSearchParams({ q, limit: String(limit) });
  if (types) p.set('types', types);
  const res = await fetch(`${getApiUrl()}/library/search?${p}`);
  if (!res.ok) throw new Error('Search failed');
  return res.json();
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

export const TYPE_LABELS: Record<string, string> = {
  act: 'Act',
  rule: 'Rule',
  notification: 'Notification',
  circular: 'Circular',
};

export const TYPE_COLORS: Record<string, string> = {
  act: 'bg-violet-100 text-violet-700',
  rule: 'bg-sky-100 text-sky-700',
  notification: 'bg-amber-100 text-amber-700',
  circular: 'bg-emerald-100 text-emerald-700',
};

/** Returns the URL to fetch a notification or circular PDF. */
export function getLibraryPdfUrl(type: 'notification' | 'circular', primaryId: number): string {
  const plural = type === 'notification' ? 'notifications' : 'circulars';
  return `${getApiUrl()}/library/${plural}/${primaryId}/pdf`;
}

/** Extract the primary title string from any document type. */
export function docTitle(doc: LibraryDoc): string {
  if (doc.type === 'act') {
    const d = doc.document;
    return `${d.act_name} — ${d.section_no}${d.section_name ? ': ' + d.section_name : ''}`;
  }
  if (doc.type === 'rule') {
    const d = doc.document;
    return `${d.act_name} — ${d.section_no}${d.section_name ? ': ' + d.section_name : ''}`;
  }
  if (doc.type === 'notification') {
    const d = doc.document;
    return d.title || d.notification_no;
  }
  const d = doc.document;
  return d.subject || d.circular_no;
}
