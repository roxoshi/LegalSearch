'use client';

import Link from 'next/link';

export default function SearchResult({ result }: { result: any }) {
  // Build tags from ML-extracted fields; fall back to court/citation snippets
  const tags: string[] = [
    ...(result.extracted_provisions ?? []).slice(0, 2),
    ...(result.extracted_statutes ?? []).slice(0, 2),
  ]
    .filter(Boolean)
    .slice(0, 4);

  if (tags.length === 0) {
    if (result.court) tags.push(result.court.split(' ').slice(0, 2).join(' '));
    if (result.citation) tags.push(result.citation.split(' ')[0]);
  }

  return (
    <Link
      href={`/document/${result.id}`}
      className={`block bg-white border rounded-xl p-5 hover:shadow-sm transition-all cursor-pointer ${
        result.is_direct_hit
          ? 'border-green-300 ring-1 ring-green-200'
          : 'border-slate-200 hover:border-slate-300'
      }`}
    >
      {/* Direct hit badge */}
      {result.is_direct_hit && (
        <div className="flex items-center gap-1.5 mb-2">
          <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-green-50 text-green-700 text-xs font-semibold rounded-full border border-green-200">
            <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.857-9.809a.75.75 0 00-1.214-.882l-3.483 4.79-1.88-1.88a.75.75 0 10-1.06 1.061l2.5 2.5a.75.75 0 001.137-.089l4-5.5z" clipRule="evenodd" />
            </svg>
            Top Result — Exact Match
          </span>
        </div>
      )}

      {/* Title + court badge + date */}
      <h3 className="font-semibold text-slate-900 text-sm leading-snug mb-2 line-clamp-2">
        {result.title || 'Untitled Document'}
      </h3>
      <div className="flex items-center gap-2 mb-2.5">
        {result.court && (
          <span className="inline-flex items-center px-2 py-0.5 bg-blue-50 text-blue-700 text-xs font-medium rounded-full whitespace-nowrap">
            {result.court}
          </span>
        )}
        {result.decision_date && (
          <span className="text-xs text-slate-400">{result.decision_date}</span>
        )}
      </div>

      {/* Preview */}
      <p className="text-sm text-slate-500 leading-relaxed line-clamp-2">
        {result.content || 'No preview available.'}
      </p>

      {/* Tags */}
      {tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-3">
          {tags.map((tag) => (
            <span
              key={tag}
              className="text-xs bg-slate-100 text-slate-600 px-2 py-0.5 rounded-md"
            >
              {tag}
            </span>
          ))}
        </div>
      )}
    </Link>
  );
}
