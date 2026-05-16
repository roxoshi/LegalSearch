'use client';

import { useEffect, useState, useCallback } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { ArrowLeftIcon, LinkIcon } from '@heroicons/react/24/outline';
import {
  resolveLibraryDoc,
  LibraryDoc,
  TYPE_LABELS,
  TYPE_COLORS,
  docTitle,
  CrossRef,
  getLibraryPdfUrl,
} from '@/lib/library';
import { CrossRefModal } from '@/components/CrossRefModal';

// Matches explore-{type}/{id} in HTML href attributes
const XREF_RE = /explore-(notification|rule|act|circular)\/(\d+)/i;

export default function LibraryDetailPage() {
  const { type, id } = useParams<{ type: string; id: string }>();
  const router = useRouter();

  // strip trailing 's' to get singular type for the API (acts→act, rules→rule, etc.)
  const docType = type?.replace(/s$/, '') ?? '';
  const primaryId = Number(id);

  const [doc, setDoc] = useState<LibraryDoc | null>(null);
  const [loading, setLoading] = useState(true);
  const [modal, setModal] = useState<{ type: string; id: number } | null>(null);

  useEffect(() => {
    if (!docType || !primaryId) return;
    setLoading(true);
    resolveLibraryDoc(docType, primaryId)
      .then(setDoc)
      .catch(() => setDoc(null))
      .finally(() => setLoading(false));
  }, [docType, primaryId]);

  // Intercept cross-reference links embedded in HTML content
  const handleContentClick = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    const anchor = (e.target as HTMLElement).closest('a');
    if (!anchor) return;
    const href = anchor.getAttribute('href') || '';
    const match = href.match(XREF_RE);
    if (match) {
      e.preventDefault();
      setModal({ type: match[1].toLowerCase(), id: Number(match[2]) });
    }
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="w-8 h-8 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (!doc) {
    return (
      <div className="p-20 text-center text-slate-500">
        Document not found.{' '}
        <Link href="/library" className="text-blue-600 hover:underline">
          Back to Library
        </Link>
      </div>
    );
  }

  const xrefs = getXrefs(doc);
  const hasHtml = hasHtmlContent(doc);

  return (
    <>
      <div className="min-h-screen bg-[#fcfcfc] text-slate-900 font-sans">
        <div className="max-w-7xl mx-auto py-10 px-6">
          {/* Back */}
          <button
            onClick={() => router.back()}
            className="flex items-center text-slate-500 hover:text-blue-600 mb-8 transition-colors text-sm"
          >
            <ArrowLeftIcon className="w-4 h-4 mr-2" />
            Back
          </button>

          {/* Header */}
          <header className="mb-8 pb-8 border-b border-slate-200">
            <span
              className={`inline-block text-xs font-medium px-2.5 py-1 rounded-full mb-3 ${TYPE_COLORS[doc.type] ?? 'bg-slate-100 text-slate-600'}`}
            >
              {TYPE_LABELS[doc.type] ?? doc.type}
            </span>
            <h1 className="text-2xl font-serif font-medium text-slate-900 leading-tight mb-3">
              {docTitle(doc)}
            </h1>
            <MetaBadges doc={doc} />
          </header>

          {/* PDF viewer layout for notifications and circulars */}
          {(doc.type === 'notification' || doc.type === 'circular') ? (
            <div className="flex gap-6">
              <div className="flex-1 min-w-0">
                <iframe
                  src={getLibraryPdfUrl(doc.type, primaryId)}
                  className="w-full rounded-2xl border border-slate-200 shadow-sm"
                  style={{ height: 'calc(100vh - 260px)', minHeight: '600px' }}
                  title={docTitle(doc)}
                />
              </div>
              <aside className="w-64 shrink-0 space-y-5">
                <MetaCard doc={doc} />
              </aside>
            </div>
          ) : (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-10">
            {/* Main content */}
            <div className="lg:col-span-2">
              <div className="bg-white p-8 rounded-2xl border border-slate-100 shadow-sm">
                {hasHtml ? (
                  // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions
                  <div
                    className="prose prose-slate max-w-none [&_a]:text-blue-600 [&_a]:cursor-pointer"
                    onClick={handleContentClick}
                    dangerouslySetInnerHTML={{ __html: getHtmlContent(doc)! }}
                  />
                ) : (
                  <div className="whitespace-pre-wrap leading-relaxed text-sm text-slate-700">
                    {getTextContent(doc) || (
                      <span className="text-slate-400 italic">No content available.</span>
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* Sidebar */}
            <aside className="space-y-5">
              <MetaCard doc={doc} />

              {xrefs.length > 0 && (
                <div className="bg-slate-50 p-5 rounded-2xl border border-slate-100">
                  <h3 className="font-semibold text-slate-900 mb-3 flex items-center gap-2 text-sm">
                    <LinkIcon className="w-4 h-4 text-slate-400" />
                    Cross-References ({xrefs.length})
                  </h3>
                  <ul className="space-y-2">
                    {xrefs.map((xr, i) => (
                      <CrossRefItem key={i} xr={xr} onOpen={setModal} />
                    ))}
                  </ul>
                </div>
              )}
            </aside>
          </div>
          )}
        </div>
      </div>

      {modal && (
        <CrossRefModal
          docType={modal.type}
          primaryId={modal.id}
          onClose={() => setModal(null)}
        />
      )}
    </>
  );
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function MetaBadges({ doc }: { doc: LibraryDoc }) {
  const badges: string[] = [];
  if (doc.type === 'act') {
    if (doc.document.chapter_no) badges.push(doc.document.chapter_no);
    if (doc.document.chapter_name) badges.push(doc.document.chapter_name);
  }
  if (doc.type === 'rule' && doc.document.act_name) badges.push(doc.document.act_name);
  if (doc.type === 'notification') {
    if (doc.document.category) badges.push(doc.document.category);
    if (doc.document.year) badges.push(String(doc.document.year));
  }
  if (doc.type === 'circular') {
    if (doc.document.category) badges.push(doc.document.category);
    if (doc.document.year) badges.push(String(doc.document.year));
  }
  if (!badges.length) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {badges.map((b) => (
        <span key={b} className="bg-slate-100 text-slate-600 text-xs px-3 py-1 rounded-full">
          {b}
        </span>
      ))}
    </div>
  );
}

function MetaCard({ doc }: { doc: LibraryDoc }) {
  const rows: { label: string; value: string }[] = [];

  if (doc.type === 'act') {
    rows.push({ label: 'Act', value: doc.document.act_name });
    if (doc.document.chapter_no)
      rows.push({ label: 'Chapter', value: `${doc.document.chapter_no}${doc.document.chapter_name ? ' — ' + doc.document.chapter_name : ''}` });
    rows.push({ label: 'Section', value: doc.document.section_no });
  } else if (doc.type === 'rule') {
    rows.push({ label: 'Rules', value: doc.document.act_name });
    rows.push({ label: 'Rule No.', value: doc.document.section_no });
  } else if (doc.type === 'notification') {
    rows.push({ label: 'Notification No.', value: doc.document.notification_no });
    if (doc.document.category) rows.push({ label: 'Category', value: doc.document.category });
    if (doc.document.issued_on)
      rows.push({ label: 'Issued On', value: new Date(doc.document.issued_on).toLocaleDateString() });
    rows.push({ label: 'Active', value: doc.document.is_active ? 'Yes' : 'No' });
    rows.push({ label: 'Amended', value: doc.document.is_amended ? 'Yes' : 'No' });
  } else {
    rows.push({ label: 'Circular No.', value: doc.document.circular_no });
    if (doc.document.category) rows.push({ label: 'Category', value: doc.document.category });
    if (doc.document.issued_on)
      rows.push({ label: 'Issued On', value: new Date(doc.document.issued_on).toLocaleDateString() });
    rows.push({ label: 'Active', value: doc.document.is_active ? 'Yes' : 'No' });
    rows.push({ label: 'Amended', value: doc.document.is_amended ? 'Yes' : 'No' });
  }

  return (
    <div className="bg-slate-50 p-5 rounded-2xl border border-slate-100">
      <h3 className="font-semibold text-slate-900 mb-3 text-sm">Details</h3>
      <dl className="space-y-3 text-sm">
        {rows.map(({ label, value }) => (
          <div key={label}>
            <dt className="text-xs uppercase tracking-wide text-slate-400 mb-0.5">{label}</dt>
            <dd className="font-medium text-slate-800">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function CrossRefItem({
  xr,
  onOpen,
}: {
  xr: CrossRef;
  onOpen: (ref: { type: string; id: number }) => void;
}) {
  return (
    <li>
      <button
        onClick={() => onOpen({ type: xr.target_type, id: xr.target_id })}
        className="w-full text-left group"
      >
        <div className="flex items-start gap-2">
          <span
            className={`shrink-0 mt-0.5 text-xs font-medium px-1.5 py-0.5 rounded ${TYPE_COLORS[xr.target_type] ?? 'bg-slate-100 text-slate-600'}`}
          >
            {TYPE_LABELS[xr.target_type] ?? xr.target_type}
          </span>
          <div className="min-w-0">
            <p className="text-xs font-medium text-slate-700 group-hover:text-blue-600 transition-colors">
              {xr.label || xr.anchor_text || `#${xr.target_id}`}
            </p>
            {xr.title && (
              <p className="text-xs text-slate-400 truncate">{xr.title}</p>
            )}
          </div>
        </div>
      </button>
    </li>
  );
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function getXrefs(doc: LibraryDoc): CrossRef[] {
  if (doc.type === 'act') return doc.document.cross_references;
  if (doc.type === 'rule') return doc.document.cross_references;
  return [];
}

function hasHtmlContent(doc: LibraryDoc): boolean {
  if (doc.type === 'act') return !!doc.document.html_content;
  if (doc.type === 'rule') return !!doc.document.html_content;
  return false;
}

function getHtmlContent(doc: LibraryDoc): string | null {
  if (doc.type === 'act') return doc.document.html_content;
  if (doc.type === 'rule') return doc.document.html_content;
  return null;
}

function getTextContent(doc: LibraryDoc): string | null {
  if (doc.type === 'act') return doc.document.content;
  if (doc.type === 'rule') return doc.document.content;
  if (doc.type === 'notification') return doc.document.content;
  if (doc.type === 'circular') return doc.document.content;
  return null;
}
