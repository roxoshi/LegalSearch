'use client';

import { useEffect, useState } from 'react';
import { XMarkIcon, ArrowTopRightOnSquareIcon } from '@heroicons/react/24/outline';
import {
  resolveLibraryDoc,
  LibraryDoc,
  TYPE_LABELS,
  TYPE_COLORS,
  docTitle,
} from '@/lib/library';

interface Props {
  docType: string;
  primaryId: number;
  onClose: () => void;
}

export function CrossRefModal({ docType, primaryId, onClose }: Props) {
  const [doc, setDoc] = useState<LibraryDoc | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    resolveLibraryDoc(docType, primaryId)
      .then(setDoc)
      .catch(() => setError('Failed to load document.'))
      .finally(() => setLoading(false));
  }, [docType, primaryId]);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  const detailUrl = doc ? `/library/${doc.type}s/${primaryId}` : null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl max-h-[80vh] flex flex-col mx-4">
        {/* Header */}
        <div className="flex items-start justify-between px-6 py-4 border-b border-slate-100 shrink-0">
          {doc && (
            <div className="flex-1 min-w-0 pr-4">
              <span
                className={`inline-block text-xs font-medium px-2 py-0.5 rounded-full mb-1.5 ${TYPE_COLORS[doc.type] ?? 'bg-slate-100 text-slate-600'}`}
              >
                {TYPE_LABELS[doc.type] ?? doc.type}
              </span>
              <h2 className="text-base font-semibold text-slate-900 leading-snug">
                {docTitle(doc)}
              </h2>
            </div>
          )}
          {loading && (
            <div className="flex-1 flex items-center gap-2">
              <div className="w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
              <span className="text-sm text-slate-500">Loading…</span>
            </div>
          )}
          <div className="flex items-center gap-1 shrink-0">
            {detailUrl && (
              <a
                href={detailUrl}
                title="Open full page"
                className="p-1.5 text-slate-400 hover:text-blue-600 rounded-lg hover:bg-slate-50 transition-colors"
              >
                <ArrowTopRightOnSquareIcon className="w-4 h-4" />
              </a>
            )}
            <button
              onClick={onClose}
              className="p-1.5 text-slate-400 hover:text-slate-700 rounded-lg hover:bg-slate-50 transition-colors"
            >
              <XMarkIcon className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="overflow-y-auto px-6 py-4 flex-1 text-sm text-slate-700">
          {error && <p className="text-red-500">{error}</p>}

          {doc && doc.type === 'act' && (
            <DocBody htmlContent={doc.document.html_content} textContent={doc.document.content} />
          )}
          {doc && doc.type === 'rule' && (
            <DocBody htmlContent={doc.document.html_content} textContent={doc.document.content} />
          )}
          {doc && doc.type === 'notification' && (
            <DocBody htmlContent={null} textContent={doc.document.content} />
          )}
          {doc && doc.type === 'circular' && (
            <DocBody htmlContent={null} textContent={doc.document.content} />
          )}
        </div>
      </div>
    </div>
  );
}

function DocBody({
  htmlContent,
  textContent,
}: {
  htmlContent: string | null;
  textContent: string | null;
}) {
  if (htmlContent) {
    return (
      <div
        className="prose prose-sm prose-slate max-w-none"
        dangerouslySetInnerHTML={{ __html: htmlContent }}
      />
    );
  }
  if (textContent) {
    return <div className="whitespace-pre-wrap leading-relaxed">{textContent}</div>;
  }
  return <p className="text-slate-400 italic">No content available.</p>;
}
