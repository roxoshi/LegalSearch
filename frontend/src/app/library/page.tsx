'use client';

import { useState, useEffect, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { MagnifyingGlassIcon } from '@heroicons/react/24/outline';
import {
  librarySearch,
  SearchHit,
  TYPE_LABELS,
  TYPE_COLORS,
} from '@/lib/library';

const ALL_TYPES = ['act', 'rule', 'notification', 'circular'];

function LibraryPageContent() {
  const searchParams = useSearchParams();
  const router = useRouter();

  const [query, setQuery] = useState(searchParams.get('q') || '');
  const [results, setResults] = useState<SearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedTypes, setSelectedTypes] = useState<string[]>([]);

  const performSearch = async (q: string, types: string[]) => {
    if (!q.trim()) return;
    setLoading(true);
    try {
      const typesParam = types.length > 0 ? types.join(',') : undefined;
      setResults(await librarySearch(q, typesParam, 40));
    } catch {
      setResults([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const q = searchParams.get('q');
    const t = searchParams.get('types') || '';
    if (q) {
      const types = t ? t.split(',') : [];
      setQuery(q);
      setSelectedTypes(types);
      performSearch(q, types);
    }
  }, [searchParams]);

  const handleSearch = (e?: React.FormEvent) => {
    e?.preventDefault();
    const params = new URLSearchParams();
    if (query) params.set('q', query);
    if (selectedTypes.length > 0) params.set('types', selectedTypes.join(','));
    router.push(`/library?${params}`);
  };

  const toggleType = (t: string) =>
    setSelectedTypes((prev) =>
      prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t],
    );

  const hasResults = results.length > 0;
  const showEmpty = !loading && !!query && !hasResults;

  return (
    <div className="h-screen flex flex-col bg-[#f8f9fa]">
      {/* Search bar */}
      <div className="bg-white border-b border-slate-200 px-5 py-3 shrink-0">
        <form onSubmit={handleSearch} className="relative max-w-xl">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search Acts, Rules, Notifications, Circulars…"
            className="w-full pl-4 pr-11 py-2 bg-slate-50 border border-slate-200 rounded-lg text-sm
              focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 outline-none transition-all"
          />
          <button
            type="submit"
            className="absolute right-2 top-1.5 p-1.5 text-slate-400 hover:text-blue-600 transition-colors"
          >
            {loading ? (
              <div className="w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
            ) : (
              <MagnifyingGlassIcon className="w-4 h-4" />
            )}
          </button>
        </form>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Filter panel */}
        <aside className="w-52 shrink-0 bg-white border-r border-slate-200 p-4 overflow-y-auto">
          <div className="mb-6">
            <h3 className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-2.5">
              Document Type
            </h3>
            <div className="space-y-2">
              {ALL_TYPES.map((t) => (
                <label key={t} className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={selectedTypes.includes(t)}
                    onChange={() => toggleType(t)}
                    className="w-3.5 h-3.5 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                  />
                  <span className="text-sm text-slate-600">{TYPE_LABELS[t]}</span>
                </label>
              ))}
            </div>
          </div>
          <button
            onClick={() => handleSearch()}
            className="w-full py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition-colors"
          >
            Apply Filters
          </button>
        </aside>

        {/* Results area */}
        <main className="flex-1 overflow-y-auto p-6">
          {hasResults ? (
            <>
              <div className="mb-4">
                <h2 className="text-base font-semibold text-slate-800">Search Results</h2>
                <p className="text-sm text-slate-400">{results.length} results found</p>
              </div>
              <div className="space-y-2">
                {results.map((hit, i) => (
                  <SearchHitCard key={`${hit.type}-${hit.primary_id}-${i}`} hit={hit} />
                ))}
              </div>
            </>
          ) : loading ? (
            <div className="flex items-center justify-center h-64">
              <div className="w-7 h-7 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
            </div>
          ) : showEmpty ? (
            <div className="flex items-center justify-center h-64">
              <p className="text-sm text-slate-400">No documents found matching your query.</p>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-center">
              <p className="text-2xl font-light text-slate-600 mb-2">GST Legal Library</p>
              <p className="text-sm text-slate-400 mb-8">
                Search across Acts, Rules, Notifications, and Circulars
              </p>
              <div className="grid grid-cols-2 gap-3 w-full max-w-sm">
                {ALL_TYPES.map((t) => (
                  <Link
                    key={t}
                    href={`/library?q=GST&types=${t}`}
                    className="flex items-center justify-center gap-2 py-3 px-4 bg-white border border-slate-200
                      rounded-xl text-sm font-medium text-slate-700 hover:border-blue-300 hover:text-blue-600
                      transition-colors shadow-sm"
                  >
                    <span
                      className={`w-2 h-2 rounded-full ${TYPE_COLORS[t]?.split(' ')[0] ?? 'bg-slate-300'}`}
                    />
                    {TYPE_LABELS[t]}s
                  </Link>
                ))}
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

function SearchHitCard({ hit }: { hit: SearchHit }) {
  const href = `/library/${hit.type}s/${hit.primary_id}`;
  return (
    <Link href={href}>
      <div className="bg-white border border-slate-200 rounded-xl p-4 hover:border-blue-300 hover:shadow-sm transition-all cursor-pointer">
        <div className="flex items-start gap-3">
          <span
            className={`mt-0.5 shrink-0 inline-block text-xs font-medium px-2 py-0.5 rounded-full ${TYPE_COLORS[hit.type] ?? 'bg-slate-100 text-slate-600'}`}
          >
            {TYPE_LABELS[hit.type] ?? hit.type}
          </span>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-slate-800 truncate">
              {hit.title || hit.label}
            </p>
            <p className="text-xs text-slate-400 mb-1">{hit.label}</p>
            {hit.snippet && (
              <p className="text-xs text-slate-500 line-clamp-2 leading-relaxed">{hit.snippet}</p>
            )}
          </div>
        </div>
      </div>
    </Link>
  );
}

export default function LibraryPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center h-screen">
          <div className="w-7 h-7 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
        </div>
      }
    >
      <LibraryPageContent />
    </Suspense>
  );
}
